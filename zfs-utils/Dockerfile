# Pinned to the host release (mozart is Ubuntu 22.04, kernel module zfs 2.1.5) so the userland
# matches the kmod. ubuntu:latest pulled 26.04 with zfs 2.4.1 userland against a 2.1.5 module.
FROM ubuntu:22.04
# Versions pinned to the host's, so the userland matches the kernel module and the collector
# resolves sanoid.conf with the same sanoid that takes the snapshots. If jammy-updates moves on,
# the build fails loudly: bump both here when the host is upgraded.
RUN apt-get update && apt-get install -y --no-install-recommends acl \
      zfsutils-linux=2.1.5-1ubuntu6~22.04.7 sanoid=2.1.0-1

# Create entrypoint script that will handle permissions
COPY ./entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

COPY ./gather-zfs-metrics.sh /scripts/gather-zfs-metrics.sh
RUN chmod +x /scripts/gather-zfs-metrics.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["sleep", "infinity"]
