# Sentinel Gujarat - developer shortcuts. Every target is a thin wrapper around
# the canonical compose invocation (CONTRACT §1.1); the raw commands are in
# README.md for machines without `make` (Windows: use Git Bash + `choco install make`,
# or copy the commands).
#
#   make env        create deploy/.env from the example (once)
#   make synthetic  generate media/synthetic/cam_1..8.mp4 + plates.json
#   make up         build + start and wait for the healthchecks (profile from deploy/.env, default cpu)
#   make seed       run the idempotent seed inside the api container (optional: SEED_ON_START=1 already seeds)
#   make logs       follow logs (S=api to select one service)
#   make test       backend pytest + frontend typecheck/build
#   make screenshots  capture every page as PNG into docs/screenshots (needs a running stack)
#   make down / restart / ps / config / gpu-config / reset-db / backup / psql / shell-api
#
# GPU VM: when deploy/.env says COMPOSE_PROFILES=gpu (written by deploy.sh --gpu)
# every target automatically adds deploy/docker-compose.gpu.yml, exactly like
# deploy.sh and reset-db.sh do. Without the override `make up` would recreate
# mediamtx from the stock (non-NVENC) image while the API keeps emitting
# h264_nvenc transcode commands, breaking every H.265 camera.

SHELL := bash
.DEFAULT_GOAL := help

ENV_FILE ?= deploy/.env
GPU_FILE := $(shell grep -qE '^COMPOSE_PROFILES=gpu' $(ENV_FILE) 2>/dev/null && echo -f deploy/docker-compose.gpu.yml)
COMPOSE  ?= docker compose -f deploy/docker-compose.yml $(GPU_FILE) --env-file $(ENV_FILE) --project-directory .
GPU_COMPOSE ?= docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.gpu.yml --env-file $(ENV_FILE) --project-directory .
S ?=
BASE_URL ?= http://host.docker.internal
# The Playwright image ships browsers only (no `playwright` npm package); the
# screenshots target installs the matching package inside the container.
PLAYWRIGHT_VERSION ?= 1.47.2
PLAYWRIGHT_IMAGE ?= mcr.microsoft.com/playwright:v$(PLAYWRIGHT_VERSION)-jammy
# How long `make up` waits for every healthcheck (seconds)
WAIT_TIMEOUT ?= 600

.PHONY: help env config gpu-config up down restart ps logs seed synthetic test test-backend test-frontend \
        screenshots reset-db backup psql shell-api pull clean

help: ## list targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'
	@echo "  compose files: $(COMPOSE)"

env: ## create deploy/.env from deploy/.env.example when missing
	@if [ ! -f $(ENV_FILE) ]; then cp deploy/.env.example $(ENV_FILE) && echo "created $(ENV_FILE)"; else echo "$(ENV_FILE) exists"; fi

config: env ## validate and print the resolved compose file (profile from deploy/.env)
	$(COMPOSE) config --quiet && echo "compose config OK ($(COMPOSE))"

gpu-config: env ## validate the gpu profile + NVENC override (renders on any machine)
	COMPOSE_PROFILES=gpu $(GPU_COMPOSE) config --quiet && echo "gpu compose config OK"

pull: env ## pull base images
	$(COMPOSE) pull --ignore-buildable

up: env ## build and start the whole stack, then wait until every healthcheck passes
	mkdir -p media/synthetic media/own media/fallback
	$(COMPOSE) up -d --build --remove-orphans --wait --wait-timeout $(WAIT_TIMEOUT)
	$(COMPOSE) ps

down: env ## stop the stack (volumes are kept)
	$(COMPOSE) down --remove-orphans

restart: env ## restart every service
	$(COMPOSE) restart

ps: env ## show service status
	$(COMPOSE) ps

logs: env ## follow logs; S=api limits to one service
	$(COMPOSE) logs -f --tail 200 $(S)

seed: env ## run the idempotent seed (departments, users, keys, POIs, watchlist); the API already seeds on start
	$(COMPOSE) exec -T api python -m app.seed

synthetic: env ## generate the synthetic camera videos (profile tools)
	mkdir -p media/synthetic
	$(COMPOSE) --profile tools run --rm synth
	@ls -la media/synthetic

test: test-backend test-frontend ## run every test suite

test-backend: env ## pytest inside the api image
	$(COMPOSE) run --rm --no-deps api pytest -q

test-frontend: ## typecheck + production build of the SPA (needs local node 24)
	cd frontend && npm ci && npm run typecheck && npm run build

screenshots: ## capture the demo pages as PNG into docs/screenshots (stack must be running; needs Internet for npm)
	mkdir -p docs/screenshots
	docker run --rm --add-host=host.docker.internal:host-gateway \
	  -v "$(CURDIR)/deploy/screenshots.mjs:/work/screenshots.mjs:ro" \
	  -v "$(CURDIR)/docs/screenshots:/work/out" \
	  -e BASE_URL=$(BASE_URL) -e SG_USERNAME=$${SG_USERNAME:-jury_admin} -e SG_PASSWORD="$${SG_PASSWORD:-Sentinel@Admin2026}" \
	  -w /work $(PLAYWRIGHT_IMAGE) sh -c \
	  "npm i --silent --no-save --no-package-lock --no-audit --no-fund playwright@$(PLAYWRIGHT_VERSION) && node /work/screenshots.mjs"
	@ls -la docs/screenshots

reset-db: env ## drop the database volume and re-seed
	bash deploy/reset-db.sh --yes

backup: env ## pg_dump into backups/
	bash deploy/backup.sh

psql: env ## open psql in the postgres container
	$(COMPOSE) exec postgres psql -U $${POSTGRES_USER:-sentinel} -d $${POSTGRES_DB:-sentinel}

shell-api: env ## shell inside the api container
	$(COMPOSE) exec api bash

clean: down ## stop and delete every volume (database, data, recordings, certificates)
	$(COMPOSE) down --volumes --remove-orphans
