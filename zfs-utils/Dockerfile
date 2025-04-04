FROM ubuntu:latest
RUN apt-get update && apt-get install -y acl zfsutils-linux

# Create entrypoint script that will handle permissions
COPY ./entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

COPY ./gather-metrics.sh /scripts/gather-metrics.sh
RUN chmod +x /scripts/gather-metrics.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["sleep", "infinity"]
