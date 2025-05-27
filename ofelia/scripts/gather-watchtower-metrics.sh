#!/bin/sh

# Install curl if not present
apk add --no-cache curl >/dev/null 2>&1

# Output file for node exporter textfile collector
METRICS_FILE="/textfile_collector/watchtower-metrics.prom"
TEMP_FILE="${METRICS_FILE}.tmp"

# Read bearer token
BEARER_TOKEN=$(cat /run/secrets/watchtower_bearer_token)

# Get hostname for labeling (you'll need to customize this per RPI)
HOSTNAME=$(hostname)
HOST_IP=$(hostname -I | awk '{print $1}' 2>/dev/null || echo "unknown")

# Scrape watchtower metrics using Docker DNS
curl -s -H "Authorization: Bearer $BEARER_TOKEN" \
     http://watchtower:8080/v1/metrics > "$TEMP_FILE" 2>/dev/null

# Check if curl was successful
if [ $? -eq 0 ] && [ -s "$TEMP_FILE" ]; then
    # Add labels to distinguish this host
    sed "s/{/{host=\"$HOST_IP\",hostname=\"$HOSTNAME\",/g" "$TEMP_FILE" > "$METRICS_FILE"
    rm "$TEMP_FILE"
    
    # Add success metric
    echo "watchtower_scrape_success{host=\"$HOST_IP\",hostname=\"$HOSTNAME\"} 1" >> "$METRICS_FILE"
else
    # Create error metric if scrape failed
    echo "# Watchtower scrape failed" > "$METRICS_FILE"
    echo "watchtower_scrape_success{host=\"$HOST_IP\",hostname=\"$HOSTNAME\"} 0" >> "$METRICS_FILE"
    rm -f "$TEMP_FILE"
fi

# Set proper ownership (adjust PUID/PGID as needed)
chown ${PUID:-1000}:${PGID:-1000} "$METRICS_FILE" 2>/dev/null || true
