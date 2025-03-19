#!/bin/sh

# Output file
METRICS_FILE="/zfs-metrics/zfs-metrics.prom"

# Clear previous metrics
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
    if ($0 ~ /([0-9.]+)%/) {
      match($0, /([0-9.]+)%/, arr);
      scrub_pct = arr[1];
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

# Set ownership of the metrics file
chown ${PUID:-1000}:${PGID:-1000} $METRICS_FILE
