#!/usr/bin/env bash
# Sentinel Gujarat - one-shot deployment for Ubuntu 22.04 (CONTRACT §1.1).
#
#   Fresh VM:   curl -fsSL https://raw.githubusercontent.com/<org>/sentinel-gujarat/main/deploy/deploy.sh \
#                 | bash -s -- --domain sentinel.example.in --email ops@example.in --gpu \
#                   --sandbox-url http://<sandbox-host> --sandbox-auth basic --sandbox-user u --sandbox-password p
#   In a clone: ./deploy/deploy.sh [options]
#
# Options:
#   --domain <fqdn>          public site: automatic HTTPS, COOKIE_SECURE=1, PUBLIC_BASE_URL/CORS https://<fqdn>,
#                            MOCK_SANDBOX=0 (unless --mock), nightly backup crontab (unless --no-cron)
#   --email <address>        ACME contact e-mail for Let's Encrypt (optional)
#   --gpu                    NVIDIA profile: anpr-*-gpu workers, NVENC MediaMTX build
#   --public-ip <ip>         IP advertised for WebRTC (MTX_WEBRTCADDITIONALHOSTS); auto-detected when omitted
#   --sandbox-url <url>      organiser catalogue host (the importer calls <url>/api/ingest); sets MOCK_SANDBOX=0
#   --sandbox-auth <type>    none | basic | bearer | header (default none)
#   --sandbox-user <name>    basic-auth user name
#   --sandbox-password <v>   basic-auth password or bearer token
#   --sandbox-header <h>     "Name: value" for --sandbox-auth header
#   --mock                   keep the built-in 50-camera mock catalogue (MOCK_SANDBOX=1) even with --domain
#   --synth / --no-synth     force / skip the synthetic camera videos (default: generate, except on a
#                            --domain deployment that points at a real --sandbox-url)
#   --no-cron                do not install the nightly pg_dump crontab line on a --domain deployment
#   --no-install             skip the apt / Docker / NVIDIA toolkit installation
#
# What it does, in order:
#   1. installs curl/git/jq, Docker Engine + Compose v2 (and the NVIDIA container toolkit with --gpu)
#   2. clones the repository into $INSTALL_DIR when not run from inside a checkout
#   3. creates deploy/.env from .env.example (never overwrites an existing one) and generates fresh
#      POSTGRES_PASSWORD / JWT_SECRET / INTERNAL_API_KEY / BULK_API_KEY for that new file; applies the options
#   4. audits every (secret) value: on a public (--domain / COOKIE_SECURE=1) deployment a default
#      POSTGRES_PASSWORD, JWT_SECRET or API key is fatal (the API refuses to start with them anyway),
#      default jury passwords and a mock catalogue are loud warnings
#   5. generates the synthetic camera videos (profile "tools") when media/synthetic is empty
#   6. docker compose up -d --build, waits for the API healthcheck, runs the seed
#   7. installs the nightly backup crontab (--domain) and prints the URL and the jury usernames
#
# Idempotent: re-running updates the stack in place (git pull is NOT done automatically).
# The database password is applied to the postgres volume on first start; to change it later use
#   ./deploy/rotate-db-password.sh --generate     (ALTER USER + deploy/.env + api restart)

set -Eeuo pipefail

REPO_URL="${REPO_URL:-https://github.com/dynatech-consultancy/sentinel-gujarat.git}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/sentinel-gujarat}"
DOMAIN=""
ACME_EMAIL=""
PUBLIC_IP=""
USE_GPU=0
DO_INSTALL=1
DO_SYNTH=""           # "" = decide later (1 unless --domain + --sandbox-url)
DO_CRON=1
KEEP_MOCK=0
SANDBOX_URL=""
SANDBOX_AUTH=""
SANDBOX_USER=""
SANDBOX_PASSWORD=""
SANDBOX_HEADER=""

log()  { printf '\033[1;34m[deploy]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[deploy] WARNING:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[deploy] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
  # In the `curl ... | bash -s -- --help` form $0 is "bash", not this file.
  if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ] && [ -r "${BASH_SOURCE[0]}" ]; then
    sed -n '2,39p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  else
    cat <<'HELP'
Sentinel Gujarat - one-shot deployment for Ubuntu 22.04.

  curl -fsSL https://raw.githubusercontent.com/<org>/sentinel-gujarat/main/deploy/deploy.sh \
    | bash -s -- --domain <fqdn> --email <address> --gpu [--sandbox-url <url> ...]

Options: --domain <fqdn>  --email <address>  --gpu  --public-ip <ip>
         --sandbox-url <url>  --sandbox-auth none|basic|bearer|header  --sandbox-user <u>
         --sandbox-password <p>  --sandbox-header "Name: value"  --mock
         --synth | --no-synth  --no-cron  --no-install  --help
Full option help: ./deploy/deploy.sh --help from a clone, or the header of deploy/deploy.sh.
HELP
  fi
  exit 0
}

need_arg() { [ $# -ge 2 ] && [ -n "$2" ] || die "option $1 needs a value (see --help)"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --domain)           need_arg "$@"; DOMAIN="$2"; shift 2 ;;
    --email)            need_arg "$@"; ACME_EMAIL="$2"; shift 2 ;;
    --public-ip)        need_arg "$@"; PUBLIC_IP="$2"; shift 2 ;;
    --sandbox-url)      need_arg "$@"; SANDBOX_URL="$2"; shift 2 ;;
    --sandbox-auth)     need_arg "$@"; SANDBOX_AUTH="$2"; shift 2 ;;
    --sandbox-user)     need_arg "$@"; SANDBOX_USER="$2"; shift 2 ;;
    --sandbox-password) need_arg "$@"; SANDBOX_PASSWORD="$2"; shift 2 ;;
    --sandbox-header)   need_arg "$@"; SANDBOX_HEADER="$2"; shift 2 ;;
    --mock)             KEEP_MOCK=1; shift ;;
    --gpu)              USE_GPU=1; shift ;;
    --synth)            DO_SYNTH=1; shift ;;
    --no-synth)         DO_SYNTH=0; shift ;;
    --no-cron)          DO_CRON=0; shift ;;
    --no-install)       DO_INSTALL=0; shift ;;
    -h|--help)          usage ;;
    *) die "unknown option: $1 (see --help)" ;;
  esac
done

# ---- option sanity (before anything is installed or written)
case "$DOMAIN" in
  *://*|*/*|*:*) die "--domain takes a bare host name (sentinel.example.in), not a URL" ;;
esac
if [ -n "$SANDBOX_URL" ]; then
  case "$SANDBOX_URL" in
    http://*|https://*) ;;
    *) die "--sandbox-url must start with http:// or https:// (got '$SANDBOX_URL')" ;;
  esac
  SANDBOX_URL="${SANDBOX_URL%/}"
  [ "$KEEP_MOCK" = 1 ] && die "--mock and --sandbox-url exclude each other"
fi
SANDBOX_AUTH="${SANDBOX_AUTH:-none}"
case "$SANDBOX_AUTH" in
  none|basic|bearer|header) ;;
  *) die "--sandbox-auth must be none, basic, bearer or header (got '$SANDBOX_AUTH')" ;;
esac
if [ -n "$SANDBOX_URL" ]; then
  case "$SANDBOX_AUTH" in
    basic)  [ -n "$SANDBOX_USER" ] && [ -n "$SANDBOX_PASSWORD" ] || die "--sandbox-auth basic needs --sandbox-user and --sandbox-password" ;;
    bearer) [ -n "$SANDBOX_PASSWORD" ] || die "--sandbox-auth bearer needs --sandbox-password <token>" ;;
    header) case "$SANDBOX_HEADER" in *:*) ;; *) die "--sandbox-auth header needs --sandbox-header \"Name: value\"" ;; esac ;;
  esac
elif [ "$SANDBOX_AUTH" != none ] || [ -n "$SANDBOX_USER$SANDBOX_PASSWORD$SANDBOX_HEADER" ]; then
  die "--sandbox-auth/--sandbox-user/--sandbox-password/--sandbox-header need --sandbox-url"
fi
# A value written with set_env must not contain the sed delimiter or a newline.
NL="$(printf '\nx')"; NL="${NL%x}"
for v in "$DOMAIN" "$ACME_EMAIL" "$PUBLIC_IP" "$SANDBOX_URL" "$SANDBOX_USER" "$SANDBOX_PASSWORD" "$SANDBOX_HEADER"; do
  case "$v" in *'|'*|*"$NL"*) die "option values must not contain '|' or newlines" ;; esac
done
if [ -z "$DO_SYNTH" ]; then
  if [ -n "$DOMAIN" ] && [ -n "$SANDBOX_URL" ]; then DO_SYNTH=0; else DO_SYNTH=1; fi
fi

# ---------------------------------------------------------------- 1. host packages
install_docker() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    log "Docker $(docker --version | awk '{print $3}' | tr -d ,) with Compose v2 already installed"
    return
  fi
  log "Installing Docker Engine + Compose plugin (get.docker.com)"
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER" || true
  sudo systemctl enable --now docker
  warn "Your user was added to the 'docker' group; log out/in (or use sudo) for docker without sudo."
}

install_nvidia_toolkit() {
  if ! command -v nvidia-smi >/dev/null 2>&1; then
    die "--gpu given but nvidia-smi is missing. Install the NVIDIA driver (>= 535) first: sudo apt install nvidia-driver-535 && reboot"
  fi
  if docker info 2>/dev/null | grep -q 'Runtimes:.*nvidia'; then
    log "NVIDIA container toolkit already configured"
    return
  fi
  log "Installing nvidia-container-toolkit"
  curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
  curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
    | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
    | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list >/dev/null
  sudo apt-get update -qq
  sudo apt-get install -y -qq nvidia-container-toolkit
  sudo nvidia-ctk runtime configure --runtime=docker
  sudo systemctl restart docker
}

if [ "$DO_INSTALL" = 1 ]; then
  if [ "$(uname -s)" = "Linux" ]; then
    if ! command -v git >/dev/null 2>&1 || ! command -v curl >/dev/null 2>&1 || ! command -v jq >/dev/null 2>&1 || ! command -v openssl >/dev/null 2>&1; then
      log "Installing curl, git, jq, openssl, ca-certificates (apt-get update first)"
      sudo apt-get update -qq || die "apt-get update failed - fix the package sources and re-run"
      sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl git ca-certificates jq openssl \
        || die "apt-get install curl git ca-certificates jq openssl failed"
    fi
    install_docker
    [ "$USE_GPU" = 1 ] && install_nvidia_toolkit
  else
    warn "Not Linux: skipping package installation (Docker Desktop must already run)"
  fi
fi

# ---------------------------------------------------------------- 2. repository
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/docker-compose.yml" ] && [ -f "$SCRIPT_DIR/../README.md" ]; then
  ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
else
  if [ ! -d "$INSTALL_DIR/.git" ]; then
    command -v git >/dev/null 2>&1 || die "git is required to clone $REPO_URL (apt-get install git, or run the script from a checkout)"
    log "Cloning $REPO_URL into $INSTALL_DIR"
    git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
  fi
  ROOT="$INSTALL_DIR"
fi
cd "$ROOT"
log "Repository: $ROOT"

# ---------------------------------------------------------------- 3. environment file
ENV_FILE="deploy/.env"
FRESH_ENV=0
if [ ! -f "$ENV_FILE" ]; then
  cp deploy/.env.example "$ENV_FILE"
  chmod 600 "$ENV_FILE" || true
  FRESH_ENV=1
  log "Created $ENV_FILE from .env.example"
fi

# get_env KEY: current value in deploy/.env (empty when absent)
get_env() { grep -E "^$1=" "$ENV_FILE" | head -1 | cut -d= -f2- || true; }

# set_env KEY VALUE: replace or append KEY=VALUE in deploy/.env
set_env() {
  local key="$1" value="$2"
  if grep -qE "^${key}=" "$ENV_FILE"; then
    sed -i.bak -E "s|^${key}=.*|${key}=${value}|" "$ENV_FILE" && rm -f "$ENV_FILE.bak"
  else
    printf '%s=%s\n' "$key" "$value" >>"$ENV_FILE"
  fi
}

# rand_hex N: N random bytes as lowercase hex (openssl, else /dev/urandom)
rand_hex() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex "$1"
  else
    od -An -N"$1" -tx1 /dev/urandom | tr -d ' \n'
  fi
}

# Compose project name (deploy/docker-compose.yml `name: sentinel`) -> volume names.
PROJECT="${COMPOSE_PROJECT_NAME:-sentinel}"
PGDATA_VOLUME="${PROJECT}_pgdata"
ROTATE_DB_PASSWORD=0

# A freshly created .env never keeps the documented default machine secrets:
# the database password, the JWT signing key and the two API keys (format sk_ +
# 40 lowercase hex, CONTRACT §2.6) are generated here, once. POSTGRES_PASSWORD is
# hex only, so the DATABASE_URL compose builds from it needs no URL-encoding and
# compose interpolation cannot trip over it. The seed reads the keys on the first
# API start, so a deployment made with this script never carries the
# .env.example values. Jury passwords are left alone on purpose (they are entered
# in the portal form) and are reported by the audit below.
if [ "$FRESH_ENV" = 1 ]; then
  set_env POSTGRES_PASSWORD "$(rand_hex 16)"
  set_env JWT_SECRET "$(rand_hex 32)"
  set_env INTERNAL_API_KEY "sk_$(rand_hex 20)"
  set_env BULK_API_KEY "sk_$(rand_hex 20)"
  log "Generated POSTGRES_PASSWORD, JWT_SECRET, INTERNAL_API_KEY and BULK_API_KEY in $ENV_FILE"
  if docker volume inspect "$PGDATA_VOLUME" >/dev/null 2>&1 || sudo -n docker volume inspect "$PGDATA_VOLUME" >/dev/null 2>&1; then
    # The postgres image applies POSTGRES_PASSWORD only when it initialises an
    # empty volume; an existing volume keeps its old password. Apply the new one
    # with ALTER USER before the API starts instead of letting it crash-loop.
    ROTATE_DB_PASSWORD=1
    warn "Volume $PGDATA_VOLUME already exists: the new POSTGRES_PASSWORD will be applied to it with ALTER USER before the API starts"
  fi
fi

if [ -n "$DOMAIN" ]; then
  log "Configuring public domain $DOMAIN (automatic HTTPS)"
  set_env CADDY_SITE_ADDRESS "$DOMAIN"
  set_env PUBLIC_BASE_URL "https://$DOMAIN"
  set_env CORS_ORIGINS "https://$DOMAIN"
  set_env COOKIE_SECURE 1
fi
[ -n "$ACME_EMAIL" ] && set_env CADDY_EMAIL "$ACME_EMAIL"

# ---- catalogue source (CONTRACT §5.22): organiser sandbox vs built-in mock
if [ -n "$SANDBOX_URL" ]; then
  log "Catalogue: organiser sandbox $SANDBOX_URL (auth $SANDBOX_AUTH); MOCK_SANDBOX=0"
  set_env MOCK_SANDBOX 0
  set_env SANDBOX_BASE_URL "$SANDBOX_URL"
  set_env SANDBOX_AUTH_TYPE "$SANDBOX_AUTH"
  set_env SANDBOX_USERNAME "$SANDBOX_USER"
  set_env SANDBOX_PASSWORD "$SANDBOX_PASSWORD"
  set_env SANDBOX_AUTH_HEADER "$SANDBOX_HEADER"
elif [ "$KEEP_MOCK" = 1 ]; then
  log "Catalogue: built-in mock (--mock); MOCK_SANDBOX=1"
  set_env MOCK_SANDBOX 1
  set_env SANDBOX_BASE_URL "http://api:8000/mock-sandbox"
  set_env SANDBOX_AUTH_TYPE none
elif [ -n "$DOMAIN" ]; then
  # Public deployment without an explicit choice: never expose the mock
  # catalogue and its unauthenticated webhook sink on the Internet.
  if [ "$(get_env MOCK_SANDBOX)" != 0 ]; then
    set_env MOCK_SANDBOX 0
    warn "MOCK_SANDBOX set to 0 for the public domain (the mock catalogue and /api/mock-sandbox/* are off)."
    warn "No --sandbox-url given: point the portal at the organiser catalogue in Settings -> Catalogue,"
    warn "or re-run with --sandbox-url <host> [--sandbox-auth ...]; pass --mock to keep the mock on purpose."
  fi
fi

# ---- WebRTC: MediaMTX must advertise an address browsers can reach (ICE)
if [ -z "$PUBLIC_IP" ]; then
  PUBLIC_IP="$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || true)"
  case "$PUBLIC_IP" in
    *[!0-9.]*|"") PUBLIC_IP="" ;;   # keep only a plain IPv4 answer
  esac
  if [ -n "$PUBLIC_IP" ] && [ "$(uname -s)" != "Linux" ] && [ -z "$DOMAIN" ]; then
    # Laptop (Docker Desktop, no domain): 127.0.0.1 is what the browser reaches; do not
    # replace it with the NAT address of the office router.
    PUBLIC_IP=""
  fi
fi
if [ -n "$PUBLIC_IP" ]; then
  log "WebRTC will advertise $PUBLIC_IP (MTX_WEBRTCADDITIONALHOSTS)"
  set_env MTX_WEBRTCADDITIONALHOSTS "$PUBLIC_IP"
elif [ "$(get_env MTX_WEBRTCADDITIONALHOSTS)" = "127.0.0.1" ] && { [ -n "$DOMAIN" ] || [ "$(uname -s)" = "Linux" ]; }; then
  warn "Public IP could not be detected and MTX_WEBRTCADDITIONALHOSTS is still 127.0.0.1:"
  warn "WebRTC will NOT connect from other machines (tiles fall back to HLS). Re-run with --public-ip <ip>."
fi

COMPOSE_FILES=(-f deploy/docker-compose.yml)
if [ "$USE_GPU" = 1 ]; then
  set_env COMPOSE_PROFILES gpu
  set_env MEDIAMTX_TRANSCODE nvenc
  set_env ANPR_MAX_CAMERAS 12
  set_env PREINDEX_MAX_CAMERAS 50
  COMPOSE_FILES+=(-f deploy/docker-compose.gpu.yml)
elif grep -qE '^COMPOSE_PROFILES=gpu' "$ENV_FILE"; then
  # Existing GPU deployment re-run without --gpu: keep the NVENC override
  # (README §3: every compose command on the VM must include it).
  COMPOSE_FILES+=(-f deploy/docker-compose.gpu.yml)
fi

# ---------------------------------------------------------------- 4. default-secret audit
#
# PUBLIC = a domain was given now, or the existing .env already says
# COOKIE_SECURE=1 / https PUBLIC_BASE_URL. The API's own guard
# (backend/app/core/config.py) exits with "refusing to start a public
# deployment with default secrets" for POSTGRES_PASSWORD / JWT_SECRET, so
# stopping here saves a ten-minute crash-loop wait; default API keys are
# refused for the same reason (they authorise camera writes and worker
# ingestion). Jury passwords stay warnings: they are entered in the portal form.
PUBLIC=0
if [ -n "$DOMAIN" ] || [ "$(get_env COOKIE_SECURE)" = 1 ]; then PUBLIC=1; fi
case "$(get_env PUBLIC_BASE_URL)" in https://*) PUBLIC=1 ;; esac

FATAL_DEFAULTS=()
WARN_DEFAULTS=()
# check_secret KEY DEFAULT fatal|warn
check_secret() {
  local key="$1" default="$2" level="$3" current
  current="$(get_env "$key")"
  [ "$current" = "$default" ] || return 0
  if [ "$level" = fatal ] && [ "$PUBLIC" = 1 ]; then
    FATAL_DEFAULTS+=("$key")
  else
    WARN_DEFAULTS+=("$key")
  fi
}
check_secret POSTGRES_PASSWORD sentinel fatal
check_secret JWT_SECRET change-me-sentinel-gujarat-2026-please fatal
check_secret INTERNAL_API_KEY sk_internal0000000000000000000000000000000000 fatal
check_secret BULK_API_KEY sk_bulk00000000000000000000000000000000000000 fatal
check_secret JURY_ADMIN_PASSWORD 'Sentinel@Admin2026' warn
check_secret JURY_OPERATOR_PASSWORD 'Sentinel@Ops2026' warn
check_secret JURY_VIEWER_PASSWORD 'Sentinel@View2026' warn
check_secret DEPT_ADMIN_PASSWORD 'Sentinel@Police2026' warn

JWT_SECRET_VALUE="$(get_env JWT_SECRET)"
if [ "${#JWT_SECRET_VALUE}" -lt 32 ] && [ "$PUBLIC" = 1 ]; then
  FATAL_DEFAULTS+=("JWT_SECRET (shorter than 32 characters)")
fi
PG_PW_VALUE="$(get_env POSTGRES_PASSWORD)"
case "$PG_PW_VALUE" in
  *[!A-Za-z0-9_.-]*)
    warn "POSTGRES_PASSWORD contains characters other than [A-Za-z0-9_.-]; compose interpolates it verbatim into DATABASE_URL"
    warn "(@ / # ? % break the URL, \$ breaks compose). Use: ./deploy/rotate-db-password.sh --generate" ;;
esac

for k in "${WARN_DEFAULTS[@]:-}"; do
  [ -n "$k" ] && warn "$k still has its default value - change it in $ENV_FILE before the hosted demo (README §3)"
done
if [ "${#FATAL_DEFAULTS[@]}" -gt 0 ]; then
  for k in "${FATAL_DEFAULTS[@]}"; do
    printf '\033[1;31m[deploy] ERROR:\033[0m %s still has its repository default value\n' "$k" >&2
  done
  printf '\033[1;31m[deploy] ERROR:\033[0m %s\n' "A public deployment (--domain / COOKIE_SECURE=1) cannot start with default secrets; the API refuses to boot with them." >&2
  printf '\033[1;31m[deploy] ERROR:\033[0m %s\n' "Fix $ENV_FILE and re-run:" >&2
  printf '  %s\n' \
    "JWT_SECRET=\$(openssl rand -hex 32)" \
    "INTERNAL_API_KEY=sk_\$(openssl rand -hex 20)   BULK_API_KEY=sk_\$(openssl rand -hex 20)" \
    "POSTGRES_PASSWORD: ./deploy/rotate-db-password.sh --generate   (applies it to the existing database as well)" >&2
  exit 1
fi
if [ "$PUBLIC" = 1 ] && [ "$(get_env MOCK_SANDBOX)" = 1 ]; then
  warn "MOCK_SANDBOX=1 on a public deployment: the portal shows the MOCK SANDBOX badge, 'Import from catalogue'"
  warn "inserts the 50 mock cameras and /api/mock-sandbox/* (catalogue + webhook sink) is reachable without"
  warn "authentication. Use --sandbox-url <organiser host> for the jury URL."
fi
if [ "$PUBLIC" = 1 ] && [ "$(get_env MOCK_SANDBOX)" = 0 ] && [ "$(get_env SANDBOX_BASE_URL)" = "http://api:8000/mock-sandbox" ]; then
  warn "SANDBOX_BASE_URL still points at the (disabled) mock: set the organiser host in Settings -> Catalogue or with --sandbox-url"
fi

# ---------------------------------------------------------------- 5. compose helpers
DC=(docker compose "${COMPOSE_FILES[@]}" --env-file "$ENV_FILE" --project-directory "$ROOT")
if ! docker info >/dev/null 2>&1; then
  if [ "$(uname -s)" = "Linux" ]; then
    DC=(sudo -E docker compose "${COMPOSE_FILES[@]}" --env-file "$ENV_FILE" --project-directory "$ROOT")
    sudo docker info >/dev/null 2>&1 || die "Docker daemon is not reachable"
  else
    die "Docker daemon is not reachable (start Docker Desktop and retry)"
  fi
fi

log "Validating compose configuration"
"${DC[@]}" config --quiet

# wait_healthy SERVICE MINUTES
wait_healthy() {
  local svc="$1" minutes="$2" i status
  for i in $(seq 1 $((minutes * 6))); do
    status="$("${DC[@]}" ps --format '{{.Service}} {{.Health}}' 2>/dev/null | awk -v s="$svc" '$1==s{print $2}')"
    [ "$status" = "healthy" ] && return 0
    if [ "$i" = $((minutes * 6)) ]; then
      "${DC[@]}" logs --tail 50 "$svc" || true
      die "$svc did not become healthy within $minutes minutes (see logs above)"
    fi
    sleep 10
  done
}

# ---------------------------------------------------------------- 6. synthetic media
mkdir -p media/synthetic media/own media/fallback
if [ "$DO_SYNTH" = 1 ] && [ ! -f media/synthetic/cam_1.mp4 ]; then
  log "Generating synthetic camera videos (profile tools, ~2-3 min on CPU)"
  "${DC[@]}" --profile tools run --rm synth
elif [ "$DO_SYNTH" = 0 ] && [ ! -f media/synthetic/cam_1.mp4 ]; then
  log "Skipping the synthetic videos (stream/1..8 stay idle; pass --synth to generate them)"
fi

# ---------------------------------------------------------------- 7. build + start
if [ "$ROTATE_DB_PASSWORD" = 1 ]; then
  log "Applying the generated POSTGRES_PASSWORD to the existing database volume"
  "${DC[@]}" up -d postgres
  wait_healthy postgres 3
  bash deploy/rotate-db-password.sh --password "$(get_env POSTGRES_PASSWORD)" --apply-only
fi

log "Building and starting the stack (first build takes 5-10 minutes)"
"${DC[@]}" up -d --build --remove-orphans

log "Waiting for the API to become healthy"
wait_healthy api 10
log "API healthy"

log "Seeding departments, users, API keys, POIs, districts, watchlist (idempotent)"
"${DC[@]}" exec -T api python -m app.seed

# ---------------------------------------------------------------- 8. nightly backup (public deployments)
CRON_LINE=""
if [ -n "$DOMAIN" ] && [ "$DO_CRON" = 1 ]; then
  if command -v crontab >/dev/null 2>&1; then
    log "Installing the nightly pg_dump crontab line (deploy/backup.sh --install-cron)"
    bash deploy/backup.sh --install-cron
    CRON_LINE="$(crontab -l 2>/dev/null | grep 'deploy/backup.sh' | head -1 || true)"
  else
    warn "crontab not found: install cron and run ./deploy/backup.sh --install-cron for nightly backups"
  fi
fi

# ---------------------------------------------------------------- 9. summary
if [ -n "$DOMAIN" ]; then
  URL="https://$DOMAIN"
else
  URL="http://localhost"
fi
if [ "$(get_env MOCK_SANDBOX)" = 1 ]; then
  CATALOGUE="built-in mock (MOCK_SANDBOX=1)"
else
  CATALOGUE="$(get_env SANDBOX_BASE_URL) (auth $(get_env SANDBOX_AUTH_TYPE))"
fi
"${DC[@]}" ps
cat <<EOS

==================================================================
 Sentinel Gujarat is up.                       (Dynatech Consultancy)
------------------------------------------------------------------
 URL          $URL
 API docs     $URL/api/docs
 Health       $URL/healthz
 Jury logins  jury_admin (admin) · jury_operator (operator) · jury_viewer (viewer)
              dept_admin_police (dept_admin, Police only)
 Passwords    JURY_*_PASSWORD / DEPT_ADMIN_PASSWORD in $ENV_FILE
 Profile      $(get_env COMPOSE_PROFILES)  ·  transcode: $(get_env MEDIAMTX_TRANSCODE)
 Catalogue    $CATALOGUE
 WebRTC host  $(get_env MTX_WEBRTCADDITIONALHOSTS)
 Backup cron  ${CRON_LINE:-not installed (./deploy/backup.sh --install-cron)}
------------------------------------------------------------------
 Firewall: allow 80/tcp, 443/tcp, 8189/tcp+udp (WebRTC ICE).
 Next: open $URL, log in as jury_admin, Cameras -> Import -> "Import from catalogue".
 Logs:  docker compose ${COMPOSE_FILES[*]} --env-file deploy/.env --project-directory . logs -f
 DB password rotation: ./deploy/rotate-db-password.sh --generate
==================================================================
EOS
