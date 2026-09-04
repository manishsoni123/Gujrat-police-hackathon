#!/usr/bin/env bash
# Sentinel Gujarat - wipe the database and re-seed (schema changes use
# create_all, so "drop the volume and restart" is the migration strategy).
#
#   ./deploy/reset-db.sh            # asks for confirmation
#   ./deploy/reset-db.sh --yes      # non-interactive
#   ./deploy/reset-db.sh --yes --with-data   # also wipes /data (crops, frames, clips, reports)
#
# Recordings (MediaMTX volume) are never touched here.

set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

YES=0
WITH_DATA=0
for arg in "$@"; do
  case "$arg" in
    --yes|-y) YES=1 ;;
    --with-data) WITH_DATA=1 ;;
    -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 1 ;;
  esac
done

ENV_FILE="deploy/.env"
[ -f "$ENV_FILE" ] || { echo "deploy/.env missing - run deploy/deploy.sh first" >&2; exit 1; }

COMPOSE_FILES=(-f deploy/docker-compose.yml)
if grep -qE '^COMPOSE_PROFILES=gpu' "$ENV_FILE"; then
  COMPOSE_FILES+=(-f deploy/docker-compose.gpu.yml)
fi
DC=(docker compose "${COMPOSE_FILES[@]}" --env-file "$ENV_FILE" --project-directory "$ROOT")
PROJECT="$("${DC[@]}" config --format json 2>/dev/null | sed -n 's/.*"name": *"\([^"]*\)".*/\1/p' | head -1)"
PROJECT="${PROJECT:-sentinel}"

echo "This deletes volume ${PROJECT}_pgdata (all cameras, reads, sightings, alerts, audit log)."
[ "$WITH_DATA" = 1 ] && echo "It ALSO deletes volume ${PROJECT}_sentinel_data (crops, frames, clips, reports)."
if [ "$YES" != 1 ]; then
  read -r -p "Type 'reset' to continue: " answer
  [ "$answer" = "reset" ] || { echo "aborted"; exit 1; }
fi

echo "[reset-db] stopping the stack"
"${DC[@]}" down --remove-orphans

echo "[reset-db] removing ${PROJECT}_pgdata"
docker volume rm -f "${PROJECT}_pgdata" >/dev/null
if [ "$WITH_DATA" = 1 ]; then
  echo "[reset-db] removing ${PROJECT}_sentinel_data"
  docker volume rm -f "${PROJECT}_sentinel_data" >/dev/null
fi

echo "[reset-db] starting the stack (the API runs create_all + seed on start)"
"${DC[@]}" up -d

echo "[reset-db] waiting for the API"
for i in $(seq 1 60); do
  status="$("${DC[@]}" ps --format '{{.Service}} {{.Health}}' 2>/dev/null | awk '$1=="api"{print $2}')"
  [ "$status" = "healthy" ] && break
  [ "$i" = 60 ] && { "${DC[@]}" logs --tail 50 api; echo "API not healthy after 10 min" >&2; exit 1; }
  sleep 10
done

echo "[reset-db] running the seed explicitly (idempotent)"
"${DC[@]}" exec -T api python -m app.seed
echo "[reset-db] done"
