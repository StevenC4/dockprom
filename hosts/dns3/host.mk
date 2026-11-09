# dns3: DNS Pi (Raspberry Pi OS, aarch64). Satellite Prometheus; mozart
# scrapes it in the satellites job. Checkout lives at /home/pi/src/dockprom.
HOST_KIND := compose
HOST_ROLE := dns
COMPOSE_FILES := docker-compose.base.yaml docker-compose.dns.yaml
