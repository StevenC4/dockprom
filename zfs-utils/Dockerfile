# Pinned to the host release (mozart is Ubuntu 22.04, kernel module zfs 2.1.5) so the userland
# matches the kmod. ubuntu:latest pulled 26.04 with zfs 2.4.1 userland against a 2.1.5 module.
FROM ubuntu:22.04
RUN apt-get update && apt-get install -y acl zfsutils-linux

# Create entrypoint script that will handle permissions
COPY ./entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

COPY ./gather-zfs-metrics.sh /scripts/gather-zfs-metrics.sh
RUN chmod +x /scripts/gather-zfs-metrics.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["sleep", "infinity"]
