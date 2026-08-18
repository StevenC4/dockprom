# stdlib only — no pip install, nothing to pin, nothing to CVE-scan. The whole service is an
# HTTP endpoint, two auth checks, a SQLite file and a handful of Slack API calls.
FROM python:3.13-slim-bookworm

WORKDIR /app
COPY alert_ack ./alert_ack

# The service DOES own state now — see alert_ack/store.py. The ts of every alert post lives here,
# and losing it means the post can never be marked resolved, so it is a volume rather than a
# tmpfs.
#
# Created owned by 65534 BEFORE the USER switch, and that ordering is load-bearing: Docker seeds a
# fresh named volume from the image path including its ownership, so this is what stops the volume
# arriving root-owned and unwritable. The one production outage this pipeline has had was a file
# the `nobody` uid could not read; that is the same failure with the same uid, so it is designed
# out here rather than diagnosed later.
RUN install -d -o 65534 -g 65534 /data
VOLUME ["/data"]

# Runs as nobody, still: it needs the write bit on exactly one directory and nothing else.
USER 65534:65534

ENV LOG_LEVEL=INFO PYTHONUNBUFFERED=1 ALERT_ACK_DB=/data/alert-ack.db
EXPOSE 8084
ENTRYPOINT ["python", "-m", "alert_ack"]
