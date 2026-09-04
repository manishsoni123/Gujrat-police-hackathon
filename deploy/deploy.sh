#!/usr/bin/env bash
# Sentinel Gujarat - one-shot deployment for Ubuntu 22.04 (CONTRACT §1.1).
#
#   Fresh VM:   curl -fsSL https://raw.githubusercontent.com/<org>/sentinel-gujarat/main/deploy/deploy.sh | bash -s -- --domain sentinel.example.in --email ops@example.in --gpu
#   In a clone: ./deploy/deploy.sh [--domain <fqdn>] [--email <acme mail>] [--gpu] [--public-ip <ip>] [--no-install] [--no-synth]
#
# What it does, in order:
#   1. installs Docker Engine + Compose v2 (and the NVIDIA container toolkit with --gpu) unless present
#   2. clones the repository into $INSTALL_DIR when not run from inside a checkout
#   3. creates deploy/.env from .env.example (never overwrites an existing one), generates fresh
#      JWT_SECRET / INTERNAL_API_KEY / BULK_API_KEY for that new file, and applies
#      --domain / --email / --gpu / --public-ip to it
#   4. warns about every (secret) value that still equals its default
#   5. generates the synthetic camera videos (profile "tools") when media/synthetic is empty
#   6. docker compose up -d --build, waits for the API healthcheck, runs the seed
#   7. prints the URL and the jury usernames
#
# Idempotent: re-running updates the stack in place (git pull is NOT done automatically).

set -Eeuo pipefail

REPO_URL="${REPO_URL:-https://github.com/dynatech-consultancy/sentinel-gujarat.git}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/sentinel-gujarat}"
DOMAIN=""
ACME_EMAIL=""
PUBLIC_IP=""
USE_GPU=0
DO_INSTALL=1
DO_SYNTH=1

log()  { printf '\033[1;34m[deploy]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[deploy] WARNING:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[deploy] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
  sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
}

while [ $# -gt 0 ]; do
  case "$1" in
    --domain)     DOMAIN="$2"; shift 2 ;;
    --email)      ACME_EMAIL="$2"; shift 2 ;;
    --public-ip)  PUBLIC_IP="$2"; shift 2 ;;
    --gpu)        USE_GPU=1; shift ;;
    --no-install) DO_INSTALL=0; shift ;;
    --no-synth)   DO_SYNTH=0; shift ;;
    -h|--help)    usage ;;
    *) die "unknown option: $1 (see --help)" ;;
  esac
done

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
    sudo apt-get install -y -qq curl git ca-certificates jq >/dev/null 2>&1 || true
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
  FRESH_ENV=1
  log "Created $ENV_FILE from .env.example"
fi

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

# A freshly created .env never keeps the documented default machine secrets:
# the JWT signing key and the two API keys (format sk_ + 40 lowercase hex, CONTRACT
# §2.6) are generated here, once. The seed reads them on the first API start, so
# a deployment made with this script never carries the .env.example keys.
# Passwords are left alone on purpose (the jury logins are entered in the portal
# form) and are reported by the audit below.
if [ "$FRESH_ENV" = 1 ]; then
  set_env JWT_SECRET "$(rand_hex 32)"
  set_env INTERNAL_API_KEY "sk_$(rand_hex 20)"
  set_env BULK_API_KEY "sk_$(rand_hex 20)"
  log "Generated JWT_SECRET, INTERNAL_API_KEY and BULK_API_KEY in $ENV_FILE"
fi

if [ -n "$DOMAIN" ]; then
  log "Configuring public domain $DOMAIN (automatic HTTPS)"
  set_env CADDY_SITE_ADDRESS "$DOMAIN"
  set_env PUBLIC_BASE_URL "https://$DOMAIN"
  set_env CORS_ORIGINS "https://$DOMAIN"
  set_env COOKIE_SECURE 1
fi
[ -n "$ACME_EMAIL" ] && set_env CADDY_EMAIL "$ACME_EMAIL"

if [ -z "$PUBLIC_IP" ] && [ -n "$DOMAIN" ]; then
  PUBLIC_IP="$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || true)"
fi
if [ -n "$PUBLIC_IP" ]; then
  log "WebRTC will advertise $PUBLIC_IP (MTX_WEBRTCADDITIONALHOSTS)"
  set_env MTX_WEBRTCADDITIONALHOSTS "$PUBLIC_IP"
fi

COMPOSE_FILES=(-f deploy/docker-compose.yml)
if [ "$USE_GPU" = 1 ]; then
  set_env COMPOSE_PROFILES gpu
  set_env MEDIAMTX_TRANSCODE nvenc
  set_env ANPR_MAX_CAMERAS 12
  set_env PREINDEX_MAX_CAMERAS 50
  COMPOSE_FILES+=(-f deploy/docker-compose.gpu.yml)
fi

# ---------------------------------------------------------------- 4. default-secret audit
check_secret() {
  local key="$1" default="$2"
  local current
  current="$(grep -E "^${key}=" "$ENV_FILE" | head -1 | cut -d= -f2- || true)"
  if [ "$current" = "$default" ]; then
    warn "$key still has its default value - change it in $ENV_FILE before the hosted demo"
    return 1
  fi
  return 0
}
DEFAULTS_LEFT=0
check_secret POSTGRES_PASSWORD sentinel || DEFAULTS_LEFT=1
check_secret JWT_SECRET change-me-sentinel-gujarat-2026-please || DEFAULTS_LEFT=1
check_secret INTERNAL_API_KEY sk_internal0000000000000000000000000000000000 || DEFAULTS_LEFT=1
check_secret BULK_API_KEY sk_bulk00000000000000000000000000000000000000 || DEFAULTS_LEFT=1
check_secret JURY_ADMIN_PASSWORD 'Sentinel@Admin2026' || DEFAULTS_LEFT=1
check_secret JURY_OPERATOR_PASSWORD 'Sentinel@Ops2026' || DEFAULTS_LEFT=1
check_secret JURY_VIEWER_PASSWORD 'Sentinel@View2026' || DEFAULTS_LEFT=1
check_secret DEPT_ADMIN_PASSWORD 'Sentinel@Police2026' || DEFAULTS_LEFT=1
if [ "$DEFAULTS_LEFT" = 1 ] && [ -n "$DOMAIN" ]; then
  warn "A public domain is configured while default secrets remain. Generate new ones with:"
  warn "  openssl rand -hex 32   (JWT_SECRET)   |   echo sk_\$(openssl rand -hex 20)   (API keys)"
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

# ---------------------------------------------------------------- 6. synthetic media
mkdir -p media/synthetic media/own media/fallback
if [ "$DO_SYNTH" = 1 ] && [ ! -f media/synthetic/cam_1.mp4 ]; then
  log "Generating synthetic camera videos (profile tools, ~2-3 min on CPU)"
  "${DC[@]}" --profile tools run --rm synth
fi

# ---------------------------------------------------------------- 7. build + start
log "Building and starting the stack (first build takes 5-10 minutes)"
"${DC[@]}" up -d --build --remove-orphans

log "Waiting for the API to become healthy"
for i in $(seq 1 60); do
  status="$("${DC[@]}" ps --format '{{.Service}} {{.Health}}' 2>/dev/null | awk '$1=="api"{print $2}')"
  if [ "$status" = "healthy" ]; then
    break
  fi
  if [ "$i" = 60 ]; then
    "${DC[@]}" logs --tail 50 api || true
    die "API did not become healthy within 10 minutes (see logs above)"
  fi
  sleep 10
done
log "API healthy"

log "Seeding departments, users, API keys, POIs, districts, watchlist (idempotent)"
"${DC[@]}" exec -T api python -m app.seed

# ---------------------------------------------------------------- 8. summary
if [ -n "$DOMAIN" ]; then
  URL="https://$DOMAIN"
else
  URL="http://localhost"
fi
"${DC[@]}" ps
cat <<EOF

==================================================================
 Sentinel Gujarat is up.                       (Dynatech Consultancy)
------------------------------------------------------------------
 URL          $URL
 API docs     $URL/api/docs
 Health       $URL/healthz
 Jury logins  jury_admin (admin) · jury_operator (operator) · jury_viewer (viewer)
              dept_admin_police (dept_admin, Police only)
 Passwords    JURY_*_PASSWORD / DEPT_ADMIN_PASSWORD in $ENV_FILE
 Profile      $(grep -E '^COMPOSE_PROFILES=' "$ENV_FILE" | cut -d= -f2)  ·  transcode: $(grep -E '^MEDIAMTX_TRANSCODE=' "$ENV_FILE" | cut -d= -f2)
------------------------------------------------------------------
 Firewall: allow 80/tcp, 443/tcp, 8189/tcp+udp (WebRTC ICE).
 Next: open $URL, log in as jury_admin, Cameras -> Import -> "Import from catalogue".
 Logs:  docker compose -f deploy/docker-compose.yml --env-file deploy/.env --project-directory . logs -f
==================================================================
EOF
