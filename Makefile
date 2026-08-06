#!make
include .env
export

SHELL := /bin/bash
HOST_PROFILE := $(if $(ROLE),$(ROLE),$(HOSTNAME))
BASE_CONFIG := docker-compose.base.yaml
HOST_COMPOSE := docker-compose.$(HOST_PROFILE).yaml

DOCKER_COMPOSE_ARGS := -f $(BASE_CONFIG)
ifneq ("$(wildcard $(HOST_COMPOSE))","")
	DOCKER_COMPOSE_ARGS += -f $(HOST_COMPOSE)
endif

.PHONY: up down pull build config test-alert-ack test-alert-rules

up:
	docker compose $(DOCKER_COMPOSE_ARGS) up -d

down:
	docker compose $(DOCKER_COMPOSE_ARGS) down

pull:
	docker compose $(DOCKER_COMPOSE_ARGS) pull

build:
	COMPOSE_BAKE=true docker compose $(DOCKER_COMPOSE_ARGS) build

config:
	docker compose $(DOCKER_COMPOSE_ARGS) config

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

