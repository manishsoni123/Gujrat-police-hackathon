#!/usr/bin/env bash
# Sentinel Gujarat - PostgreSQL backup (plan S4: nightly pg_dump).
#
#   ./deploy/backup.sh                      # dump now to backups/sentinel_<UTC stamp>.sql.gz
#   ./deploy/backup.sh --keep-days 14       # prune dumps older than N days (default 14)
#   ./deploy/backup.sh --install-cron       # add the nightly line to the CURRENT USER's crontab
#                                           # (deploy.sh does this on a --domain deployment)
#   ./deploy/backup.sh --restore <file>     # restore a .sql.gz / .sql.gz.enc into the running postgres (DESTRUCTIVE)
#
# Dumps are made with pg_dump inside the postgres container (no client tools
# needed on the host). Every dump is followed by its SHA-256 in <file>.sha256.
#
# Schedule: one hour after the API's retention purge (RETENTION_JOB_HOUR_UTC in
# deploy/.env, default 21:00 UTC -> backup 22:00 UTC = 03:30 IST) so the dump
# never runs concurrently with the purge.
#
# Confidentiality: the dump contains the settings table (catalogue credentials,
# Telegram token, webhook secrets are stored as plain jsonb) and the password
# hashes, so files are written with umask 077 into a 0700 directory. Set
# BACKUP_PASSPHRASE (in the environment, or in the crontab line) to encrypt
# every dump with AES-256-CBC (openssl enc -pbkdf2): the file is then
# sentinel_<stamp>.sql.gz.enc and --restore needs the same variable.
#
# Files under /data (crops, frames, clips, reports) are NOT included; snapshot
# the VM disk or `docker run --rm -v sentinel_sentinel_data:/data -v $PWD/backups:/b alpine tar czf /b/data.tgz /data`.

set -Eeuo pipefail
umask 077

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
    -h|--help) sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 1 ;;
  esac
done

ENV_FILE="deploy/.env"
[ -f "$ENV_FILE" ] || { echo "deploy/.env missing" >&2; exit 1; }
env_get() { grep -E "^$1=" "$ENV_FILE" | head -1 | cut -d= -f2- ; }
PGUSER="$(env_get POSTGRES_USER)"; PGUSER="${PGUSER:-sentinel}"
PGDB="$(env_get POSTGRES_DB)";     PGDB="${PGDB:-sentinel}"
CONTAINER="${POSTGRES_CONTAINER:-sentinel-postgres}"
PURGE_HOUR="$(env_get RETENTION_JOB_HOUR_UTC)"; PURGE_HOUR="${PURGE_HOUR:-21}"
case "$PURGE_HOUR" in ''|*[!0-9]*) PURGE_HOUR=21 ;; esac
BACKUP_HOUR=$(( (PURGE_HOUR + 1) % 24 ))

ensure_dir() {
  mkdir -p "$BACKUP_DIR"
  chmod 700 "$BACKUP_DIR" 2>/dev/null || true
}

case "$MODE" in
  cron)
    # Nightly, one hour after the retention purge (see header). The line runs in
    # the crontab of the user executing this script: that user must be able to
    # run `docker exec` (docker group, as set up by deploy.sh) - check with
    # `crontab -l`, not `sudo crontab -l`.
    ensure_dir
    LINE="0 $BACKUP_HOUR * * * cd $ROOT && $ROOT/deploy/backup.sh --keep-days $KEEP_DAYS >> $ROOT/backups/backup.log 2>&1"
    ( crontab -l 2>/dev/null | grep -v 'deploy/backup.sh' || true; echo "$LINE" ) | crontab -
    echo "[backup] installed in $(id -un)'s crontab (backup $(printf '%02d' "$BACKUP_HOUR"):00 UTC, purge ${PURGE_HOUR}:00 UTC):"
    echo "  $LINE"
    if [ -z "${BACKUP_PASSPHRASE:-}" ]; then
      echo "[backup] dumps are NOT encrypted; add 'BACKUP_PASSPHRASE=<secret>' above that line in 'crontab -e' to enable AES-256"
    fi
    ;;
  dump)
    ensure_dir
    STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
    OUT="$BACKUP_DIR/sentinel_${STAMP}.sql.gz"
    if [ -n "${BACKUP_PASSPHRASE:-}" ]; then
      OUT="$OUT.enc"
      echo "[backup] pg_dump $PGDB from $CONTAINER -> $OUT (gzip + AES-256-CBC)"
      docker exec "$CONTAINER" pg_dump -U "$PGUSER" -d "$PGDB" --no-owner --no-privileges --format=plain \
        | gzip -6 | openssl enc -aes-256-cbc -pbkdf2 -salt -pass env:BACKUP_PASSPHRASE -out "$OUT"
    else
      echo "[backup] pg_dump $PGDB from $CONTAINER -> $OUT"
      docker exec "$CONTAINER" pg_dump -U "$PGUSER" -d "$PGDB" --no-owner --no-privileges --format=plain \
        | gzip -6 > "$OUT"
    fi
    chmod 600 "$OUT" 2>/dev/null || true
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
    case "$RESTORE_FILE" in
      *.enc) [ -n "${BACKUP_PASSPHRASE:-}" ] || { echo "$RESTORE_FILE is encrypted: set BACKUP_PASSPHRASE" >&2; exit 1; } ;;
    esac
    if [ -f "$RESTORE_FILE.sha256" ]; then
      EXPECT="$(cat "$RESTORE_FILE.sha256")"
      if command -v sha256sum >/dev/null 2>&1; then ACTUAL="$(sha256sum "$RESTORE_FILE" | awk '{print $1}')"; else ACTUAL="$(shasum -a 256 "$RESTORE_FILE" | awk '{print $1}')"; fi
      [ "$EXPECT" = "$ACTUAL" ] || { echo "SHA-256 mismatch for $RESTORE_FILE (expected $EXPECT, got $ACTUAL)" >&2; exit 1; }
      echo "[backup] SHA-256 verified"
    fi
    echo "This drops and recreates database '$PGDB' in $CONTAINER from $RESTORE_FILE."
    read -r -p "Type 'restore' to continue: " answer
    [ "$answer" = "restore" ] || { echo "aborted"; exit 1; }
    echo "[backup] stopping API and ANPR workers"
    docker stop sentinel-api sentinel-anpr-live sentinel-anpr-preindex >/dev/null 2>&1 || true
    docker exec "$CONTAINER" psql -U "$PGUSER" -d postgres -v ON_ERROR_STOP=1 \
      -c "DROP DATABASE IF EXISTS \"$PGDB\";" -c "CREATE DATABASE \"$PGDB\" OWNER \"$PGUSER\";"
    case "$RESTORE_FILE" in
      *.enc) openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_PASSPHRASE -in "$RESTORE_FILE" | gunzip -c ;;
      *)     gunzip -c "$RESTORE_FILE" ;;
    esac | docker exec -i "$CONTAINER" psql -U "$PGUSER" -d "$PGDB" -v ON_ERROR_STOP=1 -q
    echo "[backup] restored; starting services"
    docker start sentinel-api >/dev/null
    docker start sentinel-anpr-live sentinel-anpr-preindex >/dev/null 2>&1 || true
    ;;
esac
