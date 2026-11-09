#!/bin/sh

# Output file. Everything is written to a temp file in the same directory and renamed into place
# at the end, so node_exporter never scrapes a half-written file. The temp name does not end in
# .prom, so the textfile collector ignores it.
OUTPUT_FILE="/zfs-metrics/zfs-metrics.prom"
METRICS_FILE="$OUTPUT_FILE.tmp.$$"
trap 'rm -f "$METRICS_FILE"' EXIT
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
# The dataset list and each dataset's cadence are read from sanoid.conf, mounted read-only (the
# /etc/sanoid directory, not the file, so an editor's write-and-rename is seen), so a dataset
# added to the policy is watched without editing this file or the alert rules.
#   zfs_snapshot_max_age_seconds   - 7200 if the policy takes hourlies, 108000 if dailies only.
#                                    The alert compares against this, so the threshold follows the
#                                    policy, not the dataset's name.
#   zfs_newest_snapshot_timestamp_seconds - creation time of the newest @autosnap_. A manual
#                                    snapshot must not make a dead sanoid look healthy. 0 = none,
#                                    which reads as maximally stale rather than absent.
# Datasets with autosnap = no, or with neither hourlies nor dailies, are skipped. If the config is
# not mounted, nothing is emitted and ZfsSnapshotMetricsAbsent fires.
SANOID_CONF=/etc/sanoid/sanoid.conf
echo "# HELP zfs_newest_snapshot_timestamp_seconds Creation time of the newest sanoid autosnap of a policy dataset (0 = none)" >> $METRICS_FILE
echo "# TYPE zfs_newest_snapshot_timestamp_seconds gauge" >> $METRICS_FILE
echo "# HELP zfs_snapshot_max_age_seconds Snapshot age above which the dataset's sanoid policy is considered stale" >> $METRICS_FILE
echo "# TYPE zfs_snapshot_max_age_seconds gauge" >> $METRICS_FILE
if [ -f "$SANOID_CONF" ]; then
  # Prints "dataset template max_age" per policy dataset. Values not set on the dataset or its
  # template fall back to sanoid's own defaults (hourly 48, daily 90, autosnap yes).
  awk '
    function trim(x) { gsub(/^[ \t]+|[ \t]+$/, "", x); return x }
    /^[ \t]*[#;]/ || /^[ \t]*$/ { next }
    /^[ \t]*\[/ {
      sec = trim($0); gsub(/^\[|\]$/, "", sec)
      if (sec !~ /^template_/ && !(sec in seen)) { seen[sec] = 1; order[++n] = sec }
      next
    }
    /=/ {
      k = trim(substr($0, 1, index($0, "=") - 1)); v = trim(substr($0, index($0, "=") + 1))
      if (sec ~ /^template_/) tpl[substr(sec, 10), k] = v; else ds[sec, k] = v
      next
    }
    END {
      for (i = 1; i <= n; i++) {
        d = order[i]; t = ds[d, "use_template"]
        h = ((d, "hourly") in ds) ? ds[d, "hourly"] : (((t, "hourly") in tpl) ? tpl[t, "hourly"] : 48)
        dy = ((d, "daily") in ds) ? ds[d, "daily"] : (((t, "daily") in tpl) ? tpl[t, "daily"] : 90)
        a = ((d, "autosnap") in ds) ? ds[d, "autosnap"] : (((t, "autosnap") in tpl) ? tpl[t, "autosnap"] : "yes")
        if (a == "no" || a == "0") continue
        if (h + 0 > 0) m = 7200; else if (dy + 0 > 0) m = 108000; else continue
        print d, (t == "" ? "none" : t), m
      }
    }' "$SANOID_CONF" | while read ds tpl max_age; do
    ts=$(zfs list -Hp -t snapshot -o name,creation -s creation -d 1 "$ds" 2>/dev/null \
      | awk -F'\t' '$1 ~ /@autosnap_/ {t=$2} END {print t+0}')
    echo "zfs_newest_snapshot_timestamp_seconds{dataset=\"$ds\",template=\"$tpl\"} ${ts:-0}" >> $METRICS_FILE
    echo "zfs_snapshot_max_age_seconds{dataset=\"$ds\",template=\"$tpl\"} $max_age" >> $METRICS_FILE
  done
fi

# Set ownership, then publish atomically
chown ${PUID:-1000}:${PGID:-1000} $METRICS_FILE
chmod 0644 $METRICS_FILE
mv -f $METRICS_FILE $OUTPUT_FILE
