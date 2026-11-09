#!/bin/sh

# Output file. Everything is written to a temp file in the same directory and renamed into place
# at the end, so node_exporter never scrapes a half-written file. The temp name does not end in
# .prom, so the textfile collector ignores it.
OUTPUT_FILE="/zfs-metrics/zfs-metrics.prom"
METRICS_FILE="$OUTPUT_FILE.tmp.$$"
# dash runs the EXIT trap on exit but not when killed by a signal, so turn signals into exits.
trap 'rm -f "$METRICS_FILE"' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
> $METRICS_FILE

# Add headers and pool metrics
echo "# HELP zfs_pool_size_bytes Total size of ZFS pool in bytes" >> $METRICS_FILE
echo "# TYPE zfs_pool_size_bytes gauge" >> $METRICS_FILE
echo "# HELP zfs_pool_alloc_bytes Space allocated in ZFS pool in bytes" >> $METRICS_FILE
echo "# TYPE zfs_pool_alloc_bytes gauge" >> $METRICS_FILE
echo "# HELP zfs_pool_free_bytes Free space in ZFS pool in bytes" >> $METRICS_FILE
echo "# TYPE zfs_pool_free_bytes gauge" >> $METRICS_FILE
echo "# HELP zfs_pool_frag_percent Fragmentation percentage of ZFS pool" >> $METRICS_FILE
echo "# TYPE zfs_pool_frag_percent gauge" >> $METRICS_FILE
echo "# HELP zfs_pool_dedupratio Deduplication ratio of ZFS pool" >> $METRICS_FILE
echo "# TYPE zfs_pool_dedupratio gauge" >> $METRICS_FILE
echo "# HELP zfs_pool_health Health status of ZFS pool (1=ONLINE, 0=DEGRADED/FAULTED/OFFLINE)" >> $METRICS_FILE
echo "# TYPE zfs_pool_health gauge" >> $METRICS_FILE

# Get pool metrics
zpool list -Hp -o name,size,alloc,free,frag,dedupratio,health | while read pool size alloc free frag dedup health; do
  # Convert dedup from 1.00x format to 1.00
  dedup=${dedup%x}
  
  # Convert health to numeric value
  health_value=0
  if [ "$health" = "ONLINE" ]; then
    health_value=1
  fi
  
  echo "zfs_pool_size_bytes{pool=\"$pool\"} $size" >> $METRICS_FILE
  echo "zfs_pool_alloc_bytes{pool=\"$pool\"} $alloc" >> $METRICS_FILE
  echo "zfs_pool_free_bytes{pool=\"$pool\"} $free" >> $METRICS_FILE
  echo "zfs_pool_frag_percent{pool=\"$pool\"} $frag" >> $METRICS_FILE
  echo "zfs_pool_dedupratio{pool=\"$pool\"} $dedup" >> $METRICS_FILE
  echo "zfs_pool_health{pool=\"$pool\"} $health_value" >> $METRICS_FILE
done

# Dataset metrics
echo "# HELP zfs_dataset_used_bytes Space used by dataset in bytes" >> $METRICS_FILE
echo "# TYPE zfs_dataset_used_bytes gauge" >> $METRICS_FILE
echo "# HELP zfs_dataset_available_bytes Space available to dataset in bytes" >> $METRICS_FILE
echo "# TYPE zfs_dataset_available_bytes gauge" >> $METRICS_FILE
echo "# HELP zfs_dataset_referenced_bytes Space referenced by dataset in bytes" >> $METRICS_FILE
echo "# TYPE zfs_dataset_referenced_bytes gauge" >> $METRICS_FILE
echo "# HELP zfs_dataset_compressratio Compression ratio of dataset" >> $METRICS_FILE
echo "# TYPE zfs_dataset_compressratio gauge" >> $METRICS_FILE

# Get dataset metrics
zfs list -Hp -o name,used,avail,refer,compressratio | while read dataset used avail refer compratio; do
  # Convert compression ratio from 1.00x format to 1.00
  compratio=${compratio%x}
  
  echo "zfs_dataset_used_bytes{dataset=\"$dataset\"} $used" >> $METRICS_FILE
  echo "zfs_dataset_available_bytes{dataset=\"$dataset\"} $avail" >> $METRICS_FILE
  echo "zfs_dataset_referenced_bytes{dataset=\"$dataset\"} $refer" >> $METRICS_FILE
  echo "zfs_dataset_compressratio{dataset=\"$dataset\"} $compratio" >> $METRICS_FILE
done

# Add IO stats
echo "# HELP zfs_pool_read_ops Read operations per second" >> $METRICS_FILE
echo "# TYPE zfs_pool_read_ops gauge" >> $METRICS_FILE
echo "# HELP zfs_pool_write_ops Write operations per second" >> $METRICS_FILE
echo "# TYPE zfs_pool_write_ops gauge" >> $METRICS_FILE
echo "# HELP zfs_pool_read_bytes Bytes read per second" >> $METRICS_FILE
echo "# TYPE zfs_pool_read_bytes gauge" >> $METRICS_FILE
echo "# HELP zfs_pool_write_bytes Bytes written per second" >> $METRICS_FILE
echo "# TYPE zfs_pool_write_bytes gauge" >> $METRICS_FILE

# Get IO stats and convert values with unit suffixes to plain numbers
zpool iostat -v 1 1 | awk '
/^[a-zA-Z]/ && !/^capacity/ && $2 ~ /[0-9]/ {
  pool = $1
  
  # Process read ops - remove any unit suffixes and convert to numeric value
  read_ops = $3
  gsub(/[A-Za-z]/, "", read_ops)
  
  # Process write ops - remove any unit suffixes
  write_ops = $4
  gsub(/[A-Za-z]/, "", write_ops)
  
  # Process read bytes - already in KB, convert to bytes
  read_bytes = $5 * 1024
  
  # Process write bytes - already in KB, convert to bytes
  write_bytes = $6 * 1024
  
  print "zfs_pool_read_ops{pool=\"" pool "\"} " read_ops
  print "zfs_pool_write_ops{pool=\"" pool "\"} " write_ops
  print "zfs_pool_read_bytes{pool=\"" pool "\"} " read_bytes
  print "zfs_pool_write_bytes{pool=\"" pool "\"} " write_bytes
}' >> $METRICS_FILE

# Add scrub status
echo "# HELP zfs_pool_scrub_in_progress Whether a scrub is in progress (1) or not (0)" >> $METRICS_FILE
echo "# TYPE zfs_pool_scrub_in_progress gauge" >> $METRICS_FILE
echo "# HELP zfs_pool_scrub_percent_done Percentage of scrub completed" >> $METRICS_FILE
echo "# TYPE zfs_pool_scrub_percent_done gauge" >> $METRICS_FILE

zpool status | awk '
BEGIN { pool = ""; in_scrub = 0; scrub_pct = 0; }
/^\s*pool:/ { pool = $2; }
/^\s*scan:/ {
  if ($0 ~ /scrub in progress/) {
    in_scrub = 1;
    if ($0 ~ /[0-9.]+%/) {
      temp = $0
      gsub(/.*([0-9.]+)%.*/, "\\1", temp)
      scrub_pct = temp
    }
  } else {
    in_scrub = 0;
    scrub_pct = 0;
  }
  if (pool != "") {
    print "zfs_pool_scrub_in_progress{pool=\"" pool "\"} " in_scrub;
    print "zfs_pool_scrub_percent_done{pool=\"" pool "\"} " scrub_pct;
  }
}
' >> $METRICS_FILE

# Snapshot age for the datasets under the host's sanoid policy (Phoenix Runbook 1, PHX-18).
#
# The policy is resolved by sanoid itself, not re-implemented here: `sanoid --debug --readonly`
# runs sanoid's own config loader (defaults, template_default, each use_template in order, local
# keys, toggle normalisation, path =, recursive / zfs children, process_children_only) and dumps
# the result. It takes and prunes nothing. The result is cached and re-resolved when sanoid.conf
# changes or every 10 minutes (to pick up new children of recursive sections). /etc/sanoid is
# mounted as a directory, read-only, so an editor's write-and-rename is seen.
#
# Per policy dataset (label policy = "hourly" if it takes hourlies, else "daily"):
#   zfs_newest_snapshot_timestamp_seconds - creation time of the newest @autosnap_. A manual
#       snapshot must not make a dead sanoid look healthy. 0 = none yet (maximally stale).
#   zfs_snapshot_max_age_seconds - 7200 for hourly, 108000 for daily; the alert's threshold.
# Datasets sanoid will not snapshot (autosnap off, process_children_only, neither hourlies nor
# dailies) are skipped. If sanoid.conf is missing or sanoid rejects it, nothing is emitted and
# ZfsSnapshotMetricsAbsent fires - the host's sanoid would be failing on the same config.
SANOID_DIR=/etc/sanoid
POLICY_CACHE=/tmp/sanoid-policy.tsv
echo "# HELP zfs_newest_snapshot_timestamp_seconds Creation time of the newest sanoid autosnap of a policy dataset (0 = none)" >> $METRICS_FILE
echo "# TYPE zfs_newest_snapshot_timestamp_seconds gauge" >> $METRICS_FILE
echo "# HELP zfs_snapshot_max_age_seconds Snapshot age above which the dataset's sanoid policy is considered stale" >> $METRICS_FILE
echo "# TYPE zfs_snapshot_max_age_seconds gauge" >> $METRICS_FILE
if [ -f "$SANOID_DIR/sanoid.conf" ]; then
  if [ ! -s "$POLICY_CACHE" ] || [ "$SANOID_DIR/sanoid.conf" -nt "$POLICY_CACHE" ] \
     || [ -n "$(find "$POLICY_CACHE" -mmin +10 2>/dev/null)" ]; then
    mkdir -p /tmp/sanoid-cache /tmp/sanoid-run
    # Data::Dumper output: a section opens with `'name' => {` and each key is `'k' => v,`.
    # Truthiness follows Perl, as sanoid tests it: false only for "" and "0".
    if timeout 120 sanoid --configdir "$SANOID_DIR" --cache-dir /tmp/sanoid-cache \
         --run-dir /tmp/sanoid-run --debug --readonly 2>/dev/null \
       | awk '
          function t(v) { return v != "" && v != "0" }
          /^\*+ CONFIGS \*+$/ { on = 1; next }
          !on { next }
          /^          \x27.*\x27 => \{$/ { insec = 1; split("", k); next }
          insec && /^ +\x27[a-z_]+\x27 => / {
            line = $0; sub(/^ +\x27/, "", line)
            key = line; sub(/\x27 => .*/, "", key)
            val = line; sub(/^[a-z_]+\x27 => /, "", val); sub(/,$/, "", val)
            gsub(/^\x27|\x27$/, "", val); k[key] = val; next
          }
          insec && /^ +\},?$/ {
            insec = 0
            if (!(t(k["autosnap"]) || t(k["zfs_recursion"]))) next
            if (t(k["process_children_only"])) next
            if (k["hourly"] + 0 > 0) print k["path"] "\thourly\t7200"
            else if (k["daily"] + 0 > 0) print k["path"] "\tdaily\t108000"
          }
          /^ *\};$/ { exit }' | tr -d '"\\\r' > "$POLICY_CACHE.new" \
       && [ -s "$POLICY_CACHE.new" ]; then
      mv -f "$POLICY_CACHE.new" "$POLICY_CACHE"
    else
      rm -f "$POLICY_CACHE.new" "$POLICY_CACHE"
    fi
  fi
  if [ -s "$POLICY_CACHE" ]; then
    tab=$(printf '\t')
    while IFS="$tab" read -r ds policy max_age; do
      [ -n "$ds" ] || continue
      ts=$(zfs list -Hp -t snapshot -o name,creation -s creation -d 1 "$ds" 2>/dev/null \
        | awk -F'\t' '$1 ~ /@autosnap_/ {t=$2} END {print t+0}')
      echo "zfs_newest_snapshot_timestamp_seconds{dataset=\"$ds\",policy=\"$policy\"} ${ts:-0}" >> $METRICS_FILE
      echo "zfs_snapshot_max_age_seconds{dataset=\"$ds\",policy=\"$policy\"} $max_age" >> $METRICS_FILE
    done < "$POLICY_CACHE"
  fi
fi

# Set ownership, then publish atomically
chown ${PUID:-1000}:${PGID:-1000} $METRICS_FILE
chmod 0644 $METRICS_FILE
mv -f $METRICS_FILE $OUTPUT_FILE
