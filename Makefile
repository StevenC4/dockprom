#!make
include .env

ifeq ("$(EXPORTER_ONLY)", "true")
	docker-compose-file:=docker-compose.exporters.yml
else
	docker-compose-file:=docker-compose.yml
endif

.PHONY: up down

up:
	docker compose -f ${docker-compose-file} up -d

down:
	docker compose -f ${docker-compose-file} down

pull:
	docker compose -f ${docker-compose-file} pull

build:
	docker build -t zfs-utils zfs-utils/.
