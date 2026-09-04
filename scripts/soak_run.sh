#!/usr/bin/env bash
# Soak run: start the live ANPR worker and print read / sighting / alert counts from the API
# every INTERVAL seconds (plan Day 3 "soak"; feeds the scale plan's throughput numbers).
#
#   scripts/soak_run.sh                     # compose profile cpu, 30 s interval, runs until Ctrl-C
#   INTERVAL=60 DURATION=3600 scripts/soak_run.sh
#   API=http://localhost USER=jury_admin PASS=Sentinel@Admin2026 scripts/soak_run.sh
#
# Needs curl. jq is optional (a sed fallback extracts the numbers).
set -uo pipefail

API="${API:-http://localhost}"
USER_NAME="${USER:-jury_admin}"
PASS="${PASS:-${JURY_ADMIN_PASSWORD:-Sentinel@Admin2026}}"
INTERVAL="${INTERVAL:-30}"
DURATION="${DURATION:-0}"          # 0 = until interrupted
COMPOSE="docker compose -f deploy/docker-compose.yml --env-file deploy/.env --project-directory ."
LOG="${LOG:-soak_$(date -u +%Y%m%dT%H%M%SZ).csv}"

if [ "${START_WORKER:-1}" = "1" ]; then
  echo "starting anpr-live via compose"
  $COMPOSE up -d anpr-live || echo "compose start failed (is the stack up?)" >&2
fi

json_field() {           # json_field '<json>' <key>   -> first numeric value of "key":N
  local json="$1" key="$2"
  if command -v jq >/dev/null 2>&1; then
    printf '%s' "$json" | jq -r ".$key // 0" 2>/dev/null
  else
    printf '%s' "$json" | sed -n "s/.*\"$key\":\([0-9.]*\).*/\1/p" | head -1
  fi
}

login() {
  local body
  body=$(curl -s -m 10 -X POST "$API/api/auth/login" -H 'Content-Type: application/json' \
    -d "{\"username\":\"$USER_NAME\",\"password\":\"$PASS\"}")
  TOKEN=$(printf '%s' "$body" | sed -n 's/.*"access_token":"\([^"]*\)".*/\1/p')
  [ -n "$TOKEN" ] || { echo "login failed: $body" >&2; return 1; }
}

count() {                # count <path with filters>  -> total
  local body
  body=$(curl -s -m 15 "$API/api/$1" -H "Authorization: Bearer $TOKEN")
  json_field "$body" total
}

login || exit 1
echo "ts_utc,reads_total,reads_valid,sightings,alerts_new,cameras_online,anpr_workers" | tee "$LOG"
start=$(date +%s)
while :; do
  now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  reads=$(count "detections?page_size=1&from=1970-01-01T00:00:00Z")
  valid=$(count "detections?page_size=1&valid_only=true&from=1970-01-01T00:00:00Z")
  sightings=$(count "sightings?page_size=1&from=1970-01-01T00:00:00Z")
  alerts=$(count "alerts?page_size=1&status=new")
  health=$(curl -s -m 15 "$API/api/health/summary" -H "Authorization: Bearer $TOKEN")
  online=$(json_field "$health" online)
  workers=$(json_field "$(curl -s -m 10 "$API/healthz")" anpr_workers)
  echo "$now,${reads:-?},${valid:-?},${sightings:-?},${alerts:-?},${online:-?},${workers:-?}" | tee -a "$LOG"
  if [ -n "${SHOW_LOGS:-}" ]; then
    $COMPOSE logs --no-log-prefix --tail 3 anpr-live 2>/dev/null | grep -o '"msg":"stats:[^"]*"' | tail -1
  fi
  if [ "$DURATION" != "0" ] && [ $(( $(date +%s) - start )) -ge "$DURATION" ]; then
    break
  fi
  sleep "$INTERVAL"
  # tokens last 8 h; re-login when a request starts failing
  [ -n "$(count 'alerts?page_size=1')" ] || login || true
done
echo "soak log: $LOG"
