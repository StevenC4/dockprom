# brahms: Raspberry Pi 4 (Debian 12), IoT VLAN 204; only mozart and Home
# Assistant can reach it. Exporter only. Defined 2026-11-08, NOT deployed, and
# mozart does not scrape it yet. Candidate backup host (Phoenix).
HOST_KIND := compose
HOST_ROLE := node
COMPOSE_FILES := docker-compose.node.yaml
