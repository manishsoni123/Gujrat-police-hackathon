#!/usr/bin/env bash
# Sentinel Gujarat - rotate the PostgreSQL password of a running stack.
#
#   ./deploy/rotate-db-password.sh --generate            # new random hex password (recommended)
#   ./deploy/rotate-db-password.sh --password <value>    # your own value: [A-Za-z0-9_.-] only
#   ./deploy/rotate-db-password.sh ... --apply-only      # ALTER USER + deploy/.env, no api restart (used by deploy.sh)
#
# Why a script: the postgres image applies POSTGRES_PASSWORD only when it
# initialises an EMPTY volume, while deploy/docker-compose.yml rebuilds
# DATABASE_URL from deploy/.env on every `up`. Editing .env alone therefore
# leaves the database on the old password and the API crash-loops with
# "password authentication failed". This script does the three steps in the
# right order: ALTER USER inside the postgres container (over the local socket,
# which the image trusts), write the new value into deploy/.env, recreate the
# api container with the new DATABASE_URL and wait for its healthcheck.
#
# The value must be URL-safe because compose interpolates it verbatim into
# postgresql+asyncpg://user:PASSWORD@postgres:5432/db  (@ # ? % / break the
# URL, $ breaks compose interpolation), hence the [A-Za-z0-9_.-] rule.

set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

log()  { printf '\033[1;34m[rotate-db]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[rotate-db] WARNING:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[rotate-db] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

NEW_PASSWORD=""
GENERATE=0
APPLY_ONLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --generate) GENERATE=1; shift ;;
    --password) [ $# -ge 2 ] && [ -n "$2" ] || die "--password needs a value"; NEW_PASSWORD="$2"; shift 2 ;;
    --apply-only) APPLY_ONLY=1; shift ;;
    -h|--help) sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown option: $1 (see --help)" ;;
  esac
done
[ "$GENERATE" = 1 ] || [ -n "$NEW_PASSWORD" ] || die "give --generate or --password <value>"
[ "$GENERATE" = 1 ] && [ -n "$NEW_PASSWORD" ] && die "--generate and --password exclude each other"

ENV_FILE="deploy/.env"
[ -f "$ENV_FILE" ] || die "$ENV_FILE missing - run deploy/deploy.sh first"
get_env() { grep -E "^$1=" "$ENV_FILE" | head -1 | cut -d= -f2- || true; }
set_env() {
  local key="$1" value="$2"
  if grep -qE "^${key}=" "$ENV_FILE"; then
    sed -i.bak -E "s|^${key}=.*|${key}=${value}|" "$ENV_FILE" && rm -f "$ENV_FILE.bak"
  else
    printf '%s=%s\n' "$key" "$value" >>"$ENV_FILE"
  fi
}

if [ "$GENERATE" = 1 ]; then
  if command -v openssl >/dev/null 2>&1; then
    NEW_PASSWORD="$(openssl rand -hex 16)"
  else
    NEW_PASSWORD="$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')"
  fi
fi
case "$NEW_PASSWORD" in
  *[!A-Za-z0-9_.-]*) die "the password may only contain [A-Za-z0-9_.-] (URL-safe; use --generate)" ;;
esac
[ "${#NEW_PASSWORD}" -ge 12 ] || die "use at least 12 characters (--generate gives 32 hex characters)"

if [ -n "$(get_env DATABASE_URL)" ]; then
  die "DATABASE_URL is set explicitly in $ENV_FILE; clear it (compose builds it from POSTGRES_*) or edit the URL yourself after ALTER USER"
fi

PGUSER="$(get_env POSTGRES_USER)"; PGUSER="${PGUSER:-sentinel}"
PGDB="$(get_env POSTGRES_DB)";     PGDB="${PGDB:-sentinel}"
CONTAINER="${POSTGRES_CONTAINER:-sentinel-postgres}"

COMPOSE_FILES=(-f deploy/docker-compose.yml)
grep -qE '^COMPOSE_PROFILES=gpu' "$ENV_FILE" && COMPOSE_FILES+=(-f deploy/docker-compose.gpu.yml)
DC=(docker compose "${COMPOSE_FILES[@]}" --env-file "$ENV_FILE" --project-directory "$ROOT")
DOCKER=(docker)
if ! docker info >/dev/null 2>&1; then
  if [ "$(uname -s)" = "Linux" ] && sudo -n docker info >/dev/null 2>&1; then
    DC=(sudo -E docker compose "${COMPOSE_FILES[@]}" --env-file "$ENV_FILE" --project-directory "$ROOT")
    DOCKER=(sudo docker)
  else
    die "Docker daemon is not reachable"
  fi
fi

# ---- 1. make sure postgres runs and accepts connections
if [ "$("${DOCKER[@]}" inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null || echo false)" != true ]; then
  log "Starting postgres"
  "${DC[@]}" up -d postgres
fi
for i in $(seq 1 30); do
  "${DOCKER[@]}" exec "$CONTAINER" pg_isready -U "$PGUSER" -d "$PGDB" >/dev/null 2>&1 && break
  [ "$i" = 30 ] && die "postgres did not become ready"
  sleep 2
done

# ---- 2. ALTER USER over the container-local socket (trusted by the image, no old password needed)
SQL_PASSWORD="${NEW_PASSWORD//\'/\'\'}"
log "ALTER USER \"$PGUSER\" PASSWORD '***' in $CONTAINER"
"${DOCKER[@]}" exec "$CONTAINER" psql -U "$PGUSER" -d postgres -v ON_ERROR_STOP=1 -q \
  -c "ALTER USER \"$PGUSER\" PASSWORD '$SQL_PASSWORD';" \
  || die "ALTER USER failed"

# ---- 3. verify the new password through TCP exactly like the API does
if ! "${DOCKER[@]}" exec -e PGPASSWORD="$NEW_PASSWORD" "$CONTAINER" \
     psql -h 127.0.0.1 -U "$PGUSER" -d "$PGDB" -Atqc 'select 1' >/dev/null 2>&1; then
  die "the new password was not accepted over TCP; $ENV_FILE left unchanged"
fi

# ---- 4. persist
set_env POSTGRES_PASSWORD "$NEW_PASSWORD"
log "POSTGRES_PASSWORD updated in $ENV_FILE"

if [ "$APPLY_ONLY" = 1 ]; then
  exit 0
fi

# ---- 5. recreate the api with the new DATABASE_URL and wait for it
if "${DOCKER[@]}" inspect sentinel-api >/dev/null 2>&1; then
  log "Recreating the api container with the new DATABASE_URL"
  "${DC[@]}" up -d --no-deps api
  for i in $(seq 1 60); do
    status="$("${DC[@]}" ps --format '{{.Service}} {{.Health}}' 2>/dev/null | awk '$1=="api"{print $2}')"
    [ "$status" = "healthy" ] && { log "api healthy with the new password"; exit 0; }
    sleep 5
  done
  "${DC[@]}" logs --tail 30 api || true
  die "api did not become healthy after the rotation (see logs above)"
else
  log "api container not created yet; the next 'up' uses the new password"
fi
