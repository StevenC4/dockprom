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

.PHONY: up down pull build config

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

