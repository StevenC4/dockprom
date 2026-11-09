#!make
# Host-aware entry point: the same targets on every host. What they do on this
# host comes from hosts/<host>/host.mk. See README.md, "Hosts".
#
# Keep this file GNU Make 3.81-compatible: that is what macOS ships (dalza).

SHELL := /bin/bash

# Which host this is. `hostname -s` matched the hosts/ directory on every
# machine when this was written (2026-11-08). Override with `make HOST=dns2 ...`.
HOST ?= $(shell hostname -s)
HOST_DIR := hosts/$(HOST)
KNOWN_HOSTS := $(sort $(notdir $(wildcard hosts/*)))

# Refuse to guess. Before this, an unknown host silently ran the base file alone
# and bind-mounted a non-existent prometheus/<host>, which Docker creates empty.
ifeq ($(wildcard $(HOST_DIR)/host.mk),)
$(error Unknown host '$(HOST)': no $(HOST_DIR)/host.mk. Known hosts: $(KNOWN_HOSTS). Add one, or pass HOST=<name>)
endif

# Secrets and local overrides only; everything else is versioned in hosts/.
# Optional, so a host with no environment file still parses. Template: .env.example
-include .env
include $(HOST_DIR)/host.mk

# Compose reads ${HOSTNAME} to pick prometheus/<host>. Pin it to the detected
# host so an inherited shell HOSTNAME (often an FQDN) cannot redirect the mount.
HOSTNAME := $(HOST)
TRAEFIK_HOSTNAME ?= $(HOST)
export

.PHONY: up down pull build status config host hosts test-alert-rules test-alert-ack

# Non-secret identity only. Never add a variable that could come from the environment file.
host:
	@echo "host:          $(HOST)"
	@echo "kind:          $(HOST_KIND)"
	@echo "role:          $(HOST_ROLE)"
	@echo "compose files: $(if $(COMPOSE_FILES),$(COMPOSE_FILES),-)"
	@echo "prometheus:    $(if $(wildcard prometheus/$(HOST)/prometheus.yml),prometheus/$(HOST)/,-)"

hosts:
	@for h in $(KNOWN_HOSTS); do \
		printf '%-10s %s\n' "$$h" "$$(sed -n 's/^HOST_ROLE *:= *//p' hosts/$$h/host.mk)"; \
	done

ifeq ($(HOST_KIND),compose)

$(foreach f,$(COMPOSE_FILES),$(if $(wildcard $(f)),,$(error $(HOST_DIR)/host.mk lists $(f), which does not exist)))
DOCKER_COMPOSE := docker compose $(addprefix -f ,$(COMPOSE_FILES))

up:
	$(DOCKER_COMPOSE) up -d

down:
	$(DOCKER_COMPOSE) down

pull:
	$(DOCKER_COMPOSE) pull

build:
	COMPOSE_BAKE=true $(DOCKER_COMPOSE) build

status:
	$(DOCKER_COMPOSE) ps --format 'table {{.Name}}\t{{.Status}}'

# Validate only. A bare `docker compose config` interpolates the environment
# file and prints every secret in cleartext; never make that a target again.
config:
	$(DOCKER_COMPOSE) config --quiet && echo "compose config OK for $(HOST)"

else ifeq ($(HOST_KIND),launchd)

# macOS: Docker runs inside a Linux VM, so a node_exporter container would
# report on the VM, not the Mac, and would live or die with Docker Desktop.
# The exporter runs natively under launchd instead.
LAUNCHD := $(HOST_DIR)/launchd.sh

up:
	$(LAUNCHD) install

down:
	$(LAUNCHD) uninstall

status:
	$(LAUNCHD) status

pull build:
	@echo "$(HOST): nothing to $@ (native node_exporter; upgrade with brew, then make up)"

config:
	plutil -lint $(HOST_DIR)/*.plist

else
$(error $(HOST_DIR)/host.mk: HOST_KIND must be 'compose' or 'launchd', got '$(HOST_KIND)')
endif

# Alert rules that depend on a GATE rather than a threshold, asserted in both directions —
# silent on the healthy state, still firing on the fault. See the header of the test file for
# why only some rules are covered. promtool ships in the prometheus image, so this needs no
# tooling the repo does not already pull.
test-alert-rules:
	docker run --rm -v $(PWD)/prometheus/mozart:/w -w /w --entrypoint promtool \
		prom/prometheus:latest test rules alert.rules.test.yaml

# alert-ack is the only code in this repo, so its suite runs on its own. In a container, like
# everything else here — there is no host virtualenv to keep in sync.
test-alert-ack:
	docker run --rm -v $(PWD)/alert-ack:/app -w /app python:3.13-slim \
		sh -c "pip install -q --root-user-action=ignore pytest && python -m pytest -q"
