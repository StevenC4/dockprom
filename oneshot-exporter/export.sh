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

collect() {
  tmp="${OUT_FILE}.$$"

  {
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

    echo "$TARGETS" | while IFS='|' read -r name max_age service; do
      [ -n "${name:-}" ] || continue
      case "$name" in \#*) continue ;; esac
      name=$(echo "$name" | tr -d ' ')
      max_age=$(echo "${max_age:-0}" | tr -d ' ')
      service=$(echo "${service:-unknown}" | tr -d ' ')
      lbl="container=\"$name\",service=\"$service\""

      echo "oneshot_job_max_age_seconds{$lbl} $max_age"

      if ! state=$(docker inspect -f '{{.State.Running}}|{{.State.ExitCode}}|{{.State.FinishedAt}}' "$name" 2>/dev/null); then
        # The container does not exist. This is the prune failure mode, and it is the single
        # most important line in this file: it is the state that was invisible for 9 days.
        echo "oneshot_job_present{$lbl} 0"
        continue
      fi

      echo "oneshot_job_present{$lbl} 1"
      running=${state%%|*}
      rest=${state#*|}
      exit_code=${rest%%|*}
      finished=${rest#*|}

      [ "$running" = "true" ] && echo "oneshot_job_running{$lbl} 1" || echo "oneshot_job_running{$lbl} 0"
      echo "oneshot_job_last_exit_code{$lbl} ${exit_code:-0}"
      echo "oneshot_job_last_finish_seconds{$lbl} $(to_epoch "$finished")"
    done
  } > "$tmp"

  # Atomic replace. node_exporter re-reads this file on every scrape, so a partial write would
  # be served as a malformed collector and poison the whole textfile directory for that scrape.
  # (The existing zfs collector writes in place and has this race; not changed here.)
  mv "$tmp" "$OUT_FILE"
}

log "watching $(echo "$TARGETS" | grep -c . || true) targets, every ${INTERVAL}s -> ${OUT_FILE}"

while :; do
  # Never exit on a transient Docker hiccup: this process dying is itself an unmonitored
  # failure, and the file going stale is caught by node_textfile_mtime_seconds either way.
  collect || log "collection failed, will retry in ${INTERVAL}s"
  sleep "$INTERVAL"
done
