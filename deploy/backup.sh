#!/usr/bin/env bash
# Sentinel Gujarat - PostgreSQL backup (plan S4: nightly pg_dump).
#
#   ./deploy/backup.sh                      # dump now to backups/sentinel_<UTC stamp>.sql.gz
#   ./deploy/backup.sh --keep-days 14       # prune dumps older than N days (default 14)
#   ./deploy/backup.sh --install-cron       # install the nightly root crontab line (02:30 IST = 21:00 UTC)
#   ./deploy/backup.sh --restore <file.sql.gz>   # restore into the running postgres (DESTRUCTIVE)
#
# Dumps are made with pg_dump inside the postgres container (no client tools
# needed on the host). Every dump is followed by its SHA-256 in <file>.sha256.
# Files under /data (crops, frames, clips, reports) are NOT included; snapshot
# the VM disk or `docker run --rm -v sentinel_sentinel_data:/data -v $PWD/backups:/b alpine tar czf /b/data.tgz /data`.

set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

BACKUP_DIR="${BACKUP_DIR:-$ROOT/backups}"
KEEP_DAYS=14
MODE=dump
RESTORE_FILE=""

while [ $# -gt 0 ]; do
  case "$1" in
    --keep-days) KEEP_DAYS="$2"; shift 2 ;;
    --install-cron) MODE=cron; shift ;;
    --restore) MODE=restore; RESTORE_FILE="$2"; shift 2 ;;
    -h|--help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 1 ;;
  esac
done

ENV_FILE="deploy/.env"
[ -f "$ENV_FILE" ] || { echo "deploy/.env missing" >&2; exit 1; }
env_get() { grep -E "^$1=" "$ENV_FILE" | head -1 | cut -d= -f2- ; }
PGUSER="$(env_get POSTGRES_USER)"; PGUSER="${PGUSER:-sentinel}"
PGDB="$(env_get POSTGRES_DB)";     PGDB="${PGDB:-sentinel}"
CONTAINER="${POSTGRES_CONTAINER:-sentinel-postgres}"

case "$MODE" in
  cron)
    # Nightly at 21:00 UTC (02:30 IST), after the API's retention purge hour.
    LINE="0 21 * * * cd $ROOT && $ROOT/deploy/backup.sh --keep-days $KEEP_DAYS >> $ROOT/backups/backup.log 2>&1"
    mkdir -p "$BACKUP_DIR"
    ( crontab -l 2>/dev/null | grep -v 'deploy/backup.sh' ; echo "$LINE" ) | crontab -
    echo "[backup] installed crontab line:"
    echo "  $LINE"
    ;;
  dump)
    mkdir -p "$BACKUP_DIR"
    STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
    OUT="$BACKUP_DIR/sentinel_${STAMP}.sql.gz"
    echo "[backup] pg_dump $PGDB from $CONTAINER -> $OUT"
    docker exec "$CONTAINER" pg_dump -U "$PGUSER" -d "$PGDB" --no-owner --no-privileges --format=plain \
      | gzip -6 > "$OUT"
    if command -v sha256sum >/dev/null 2>&1; then
      sha256sum "$OUT" | awk '{print $1}' > "$OUT.sha256"
    else
      shasum -a 256 "$OUT" | awk '{print $1}' > "$OUT.sha256"
    fi
    echo "[backup] $(du -h "$OUT" | cut -f1) sha256=$(cat "$OUT.sha256")"
    echo "[backup] pruning dumps older than $KEEP_DAYS days"
    find "$BACKUP_DIR" -name 'sentinel_*.sql.gz*' -type f -mtime +"$KEEP_DAYS" -print -delete || true
    ;;
  restore)
    [ -f "$RESTORE_FILE" ] || { echo "no such file: $RESTORE_FILE" >&2; exit 1; }
    echo "This drops and recreates database '$PGDB' in $CONTAINER from $RESTORE_FILE."
    read -r -p "Type 'restore' to continue: " answer
    [ "$answer" = "restore" ] || { echo "aborted"; exit 1; }
    echo "[backup] stopping API and ANPR workers"
    docker stop sentinel-api sentinel-anpr-live sentinel-anpr-preindex >/dev/null 2>&1 || true
    docker exec "$CONTAINER" psql -U "$PGUSER" -d postgres -v ON_ERROR_STOP=1 \
      -c "DROP DATABASE IF EXISTS \"$PGDB\";" -c "CREATE DATABASE \"$PGDB\" OWNER \"$PGUSER\";"
    gunzip -c "$RESTORE_FILE" | docker exec -i "$CONTAINER" psql -U "$PGUSER" -d "$PGDB" -v ON_ERROR_STOP=1 -q
    echo "[backup] restored; starting services"
    docker start sentinel-api >/dev/null
    docker start sentinel-anpr-live sentinel-anpr-preindex >/dev/null 2>&1 || true
    ;;
esac
