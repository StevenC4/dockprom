# chopin: Raspberry Pi 3B+ (Debian 12), Server VLAN 205. Exporter only: no
# Traefik, Watchtower or Prometheus of its own. Defined 2026-11-08, NOT deployed,
# and mozart does not scrape it yet.
HOST_KIND := compose
HOST_ROLE := node
COMPOSE_FILES := docker-compose.node.yaml
