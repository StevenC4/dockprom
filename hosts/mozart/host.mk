# mozart: primary server (Ubuntu 22.04, x86_64). Full stack, and the only
# Prometheus with rule_files and an Alertmanager (owns alerting de facto, PHX-35).
HOST_KIND := compose
HOST_ROLE := primary
COMPOSE_FILES := docker-compose.base.yaml docker-compose.mozart.yaml
