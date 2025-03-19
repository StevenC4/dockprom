#!/bin/bash

# Set ownership of the output directory based on PUID/PGID
if [ ! -z "$PUID" ] && [ ! -z "$PGID" ]; then
  mkdir -p /zfs-metrics
  chown -R $PUID:$PGID /zfs-metrics
  
  # Set default ACLs so new files inherit the ownership
  setfacl -d -m u:$PUID:rwx /zfs-metrics
  setfacl -d -m g:$PGID:rwx /zfs-metrics
  
  echo "Set ownership and default ACLs on /zfs-metrics to $PUID:$PGID"
fi

# Run the command
exec "$@"
