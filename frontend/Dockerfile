# Sentinel Gujarat - web image (CONTRACT §1.1 "web"): React SPA built with
# Node 24, served by Caddy 2 which also reverse-proxies the API, WebSockets,
# MediaMTX (WHEP/HLS/playback) and stored files (deploy/Caddyfile).
#
# Build context is the repository root:
#   docker build -f frontend/Dockerfile -t sentinel-web .

# ---------------------------------------------------------------- stage 1: build
FROM node:24-alpine AS build

WORKDIR /app
ENV CI=true \
    NODE_ENV=production \
    NPM_CONFIG_UPDATE_NOTIFIER=false \
    NPM_CONFIG_FUND=false

# Dependencies first so source edits do not invalidate the npm cache layer.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --include=dev --no-audit --progress=false

COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------- stage 2: runtime
FROM caddy:2-alpine

LABEL org.opencontainers.image.title="Sentinel Gujarat web" \
      org.opencontainers.image.vendor="Dynatech Consultancy" \
      org.opencontainers.image.licenses="Apache-2.0"

COPY deploy/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/dist /srv

# Fail the build early if the Caddyfile does not parse with the shipped Caddy.
RUN CADDY_SITE_ADDRESS=:80 CADDY_ACME_EMAIL_LINE= caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile

ENV CADDY_SITE_ADDRESS=:80 \
    CADDY_EMAIL= \
    CADDY_ACME_EMAIL_LINE=

EXPOSE 80 443 443/udp
# caddy:2 default CMD: caddy run --config /etc/caddy/Caddyfile --adapter caddyfile
