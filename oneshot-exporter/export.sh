#!/bin/sh
# Exports the liveness of scheduled one-shot job containers as Prometheus metrics.
#
# WHY THIS EXISTS
# Ofelia has no metrics endpoint, no UI and no API — its only observability surface is three
# logging middlewares (mail/save/slack). So a job that stops FIRING ALTOGETHER is invisible:
# nothing is written, nothing errors, and the stack looks healthy. That is the shape of the
# July 2026 outage, where `docker system prune` removed the exited one-shot containers and
# every Ofelia tick failed with "No such container" for 9 days with no signal anywhere.
#
# Rather than ask every job to report for itself (which means touching six codebases and only
# ever covers the jobs that were remembered), this reads the state Docker already keeps for
# every container: has it finished, when, and with what exit code. One place, no cooperation
# required from the jobs, and it works identically for a Go binary, a Python one-shot and a
# Playwright scraper.
#
# WHAT IT DOES NOT KNOW
# Exit code 0 means the process ended cleanly, NOT that it did useful work. A job that exits 0
# having silently fetched nothing looks healthy here. That gap is deliberate and is covered
# separately by job-emitted metrics where it matters — nh22_source_* is the existing example,
# and it is the reason those alerts stay in place alongside these.

set -eu

OUT_DIR="${ONESHOT_OUT_DIR:-/textfile_collector}"
OUT_FILE="${OUT_DIR}/${ONESHOT_OUT_NAME:-oneshot_jobs.prom}"
INTERVAL="${ONESHOT_INTERVAL:-60}"

# Targets: one "container|max_age_seconds|service" per line, from the compose service.
# max_age_seconds is the job's own staleness threshold, emitted as a metric so a single generic
# alert rule can compare each job against its own cadence — a weekly report and a 15-minute poll
# cannot share one number, and hardcoding a rule per job would drift from the schedules.
TARGETS="${ONESHOT_TARGETS:-}"

# Schedulers: one "container|service" per line. These are the long-running daemons that START
# the one-shot jobs above, and they are watched for a different reason.
#
# The jobs above are watched by their EFFECT: a dead scheduler makes every one of them go stale,
# which OneShotJobStale reports accurately — that is how the 2026-07-31 outage was caught, 3.5h
# in. What it cannot say is WHY. Two jobs going stale independently and "the scheduler exited"
# are the same picture from the outside, so the reader has to go and find that out, and the
# alert lands as a warning per job rather than one critical for the host.
#
# So this is not redundant coverage, it is the CAUSE next to the symptom. It also closes a real
# blind spot at the edges: a scheduler that dies while every job happens to be inside its budget
# is invisible until the budgets expire, and one that crash-loops on a bad config never shows up
# at all. Ofelia in particular exits 1 on an unparseable config having scheduled nothing, and
# says so in one line of stderr in a container nobody tails.
DAEMONS="${SCHEDULER_TARGETS:-}"

log() { echo "[oneshot-exporter] $*" >&2; }

# Docker reports FinishedAt as RFC3339Nano, and the zero value for a container that has never
# run is 0001-01-01T00:00:00Z. Emit 0 for that rather than a nonsensical negative epoch, so the
# alert rules can distinguish "never ran" from "ran long ago" with a > 0 guard.
to_epoch() {
  ts="$1"
  case "$ts" in
    0001-01-01*|"") echo 0; return ;;
  esac
  date -u -d "$ts" +%s 2>/dev/null || echo 0
}

# Refuses anything that is not well-formed Prometheus text. This is not belt-and-braces: node
# _exporter skips a *.prom file it cannot parse (setting node_textfile_scrape_error=1), so ONE
# malformed line does not degrade this collector, it removes it — and a corrupt file then looks
# identical to a missing exporter while taking every scheduled-job alert blind with it. Publishing
# nothing is strictly better than publishing garbage, because a file that stops advancing trips
# OneShotExporterStale, which says what actually happened.
validate() {
  f="$1"
  [ -s "$f" ] || return 1
  awk '
    /^#/ { next }
    /^[a-zA-Z_][a-zA-Z0-9_]*\{[^}]*\} -?[0-9]+(\.[0-9]+)?$/ { good++; next }
    { bad++ }
    END { exit (bad > 0 || good == 0) ? 1 : 0 }
  ' "$f"
}

build_metrics() {
    echo "# HELP oneshot_job_present Whether the job's container exists at all (0 means it was removed, e.g. by docker system prune)."
    echo "# TYPE oneshot_job_present gauge"
    echo "# HELP oneshot_job_running Whether the job's container is executing right now."
    echo "# TYPE oneshot_job_running gauge"
    echo "# HELP oneshot_job_last_finish_seconds Unix time the job last finished; 0 if it has never run."
    echo "# TYPE oneshot_job_last_finish_seconds gauge"
    echo "# HELP oneshot_job_last_exit_code Exit code of the job's last completed run."
    echo "# TYPE oneshot_job_last_exit_code gauge"
    echo "# HELP oneshot_job_max_age_seconds Staleness budget for this job, derived from its schedule."
    echo "# TYPE oneshot_job_max_age_seconds gauge"
    echo "# HELP oneshot_job_created_seconds Unix time the job's container was created."
    echo "# TYPE oneshot_job_created_seconds gauge"

    echo "$TARGETS" | while IFS='|' read -r name max_age service; do
      [ -n "${name:-}" ] || continue
      case "$name" in \#*) continue ;; esac
      name=$(echo "$name" | tr -d ' ')
      max_age=$(echo "${max_age:-0}" | tr -d ' ')
      service=$(echo "${service:-unknown}" | tr -d ' ')
      lbl="container=\"$name\",service=\"$service\""

      echo "oneshot_job_max_age_seconds{$lbl} $max_age"

      # .Created is read alongside the run state because "has never finished" is not on its own
      # an actionable statement about a one-shot: a container recreated an hour ago and a
      # container recreated last month look identical through FinishedAt, and only the second
      # one is a problem. Publishing creation time lets OneShotJobNeverRan ask how long the
      # container has been waiting and compare that against the job's own budget, instead of
      # against a flat timer that no long-cadence job can satisfy. See that rule for the
      # observed failure: a rebuild on 2026-08-05 reset FinishedAt on the weekly and monthly
      # garmin reports, which then warned every 2h against schedules that were days and weeks
      # away — the monthly would have kept going until September.
      if ! state=$(docker inspect -f '{{.State.Running}}|{{.State.ExitCode}}|{{.State.FinishedAt}}|{{.Created}}' "$name" 2>/dev/null); then
        # The container does not exist. This is the prune failure mode, and it is the single
        # most important line in this file: it is the state that was invisible for 9 days.
        echo "oneshot_job_present{$lbl} 0"
        continue
      fi

      echo "oneshot_job_present{$lbl} 1"
      running=${state%%|*}
      rest=${state#*|}
      exit_code=${rest%%|*}
      rest=${rest#*|}
      finished=${rest%%|*}
      created=${rest#*|}

      [ "$running" = "true" ] && echo "oneshot_job_running{$lbl} 1" || echo "oneshot_job_running{$lbl} 0"
      echo "oneshot_job_last_exit_code{$lbl} ${exit_code:-0}"
      echo "oneshot_job_last_finish_seconds{$lbl} $(to_epoch "$finished")"
      echo "oneshot_job_created_seconds{$lbl} $(to_epoch "$created")"
    done

    echo "# HELP scheduler_daemon_present Whether the scheduler's container exists at all."
    echo "# TYPE scheduler_daemon_present gauge"
    echo "# HELP scheduler_daemon_running Whether the scheduler is running right now. 0 means nothing it schedules will fire."
    echo "# TYPE scheduler_daemon_running gauge"
    echo "# HELP scheduler_daemon_last_exit_code Exit code from the scheduler's last exit; 0 while it is running cleanly."
    echo "# TYPE scheduler_daemon_last_exit_code gauge"
    echo "# HELP scheduler_daemon_restart_count Docker's restart counter for the scheduler, for spotting a crash loop."
    echo "# TYPE scheduler_daemon_restart_count gauge"

    echo "$DAEMONS" | while IFS='|' read -r name service; do
      [ -n "${name:-}" ] || continue
      case "$name" in \#*) continue ;; esac
      name=$(echo "$name" | tr -d ' ')
      service=$(echo "${service:-unknown}" | tr -d ' ')
      lbl="container=\"$name\",service=\"$service\""

      if ! state=$(docker inspect -f '{{.State.Running}}|{{.State.ExitCode}}|{{.RestartCount}}' "$name" 2>/dev/null); then
        echo "scheduler_daemon_present{$lbl} 0"
        continue
      fi

      echo "scheduler_daemon_present{$lbl} 1"
      running=${state%%|*}
      rest=${state#*|}
      exit_code=${rest%%|*}
      restarts=${rest#*|}

      [ "$running" = "true" ] && echo "scheduler_daemon_running{$lbl} 1" || echo "scheduler_daemon_running{$lbl} 0"
      echo "scheduler_daemon_last_exit_code{$lbl} ${exit_code:-0}"
      echo "scheduler_daemon_restart_count{$lbl} ${restarts:-0}"
    done
}

collect() {
  tmp="${OUT_FILE}.$$"

  # `collect` is called as `collect || log`, which suppresses errexit for everything inside it.
  # So a failure mid-build does NOT abort — it just yields a short file. Both the status check
  # and the validation below therefore have to be explicit; relying on `set -e` here does not
  # work, and the original version moved whatever it produced into place unconditionally.
  if ! build_metrics > "$tmp"; then
    log "metric build failed; keeping the previous file rather than publishing a partial one"
    rm -f "$tmp"
    return 1
  fi

  if ! validate "$tmp"; then
    log "refusing to publish malformed metrics; keeping the previous file"
    rm -f "$tmp"
    return 1
  fi

  # Atomic replace. node_exporter re-reads this file on every scrape, so a partial write would
  # be served as a malformed collector and poison the whole textfile directory for that scrape.
  # (The existing zfs collector writes in place and has this race; not changed here.)
  mv "$tmp" "$OUT_FILE"
}

# Both counts skip blank and #-commented lines, so the number matches what is actually
# inspected. (It previously counted every non-empty line, which the comments in the compose
# target list inflated.)
count_targets() { echo "$1" | grep -cE '^[[:space:]]*[^#[:space:]]' || true; }
log "watching $(count_targets "$TARGETS") jobs + $(count_targets "$DAEMONS") schedulers, every ${INTERVAL}s -> ${OUT_FILE}"

while :; do
  # Never exit on a transient Docker hiccup: this process dying is itself an unmonitored
  # failure, and the file going stale is caught by node_textfile_mtime_seconds either way.
  collect || log "collection failed, will retry in ${INTERVAL}s"
  sleep "$INTERVAL"
done
