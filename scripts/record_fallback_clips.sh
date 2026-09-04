#!/usr/bin/env bash
# Record 10-minute fallback clips of chosen sandbox cameras (plan 4.4 / S4) with stream copy.
#
#   scripts/record_fallback_clips.sh <rtsp base> <id> [<id> ...]
#   scripts/record_fallback_clips.sh rtsp://<host>:8554 1 3 6            # -> media/fallback/1.mp4 ...
#   DURATION=300 OUT=media/fallback scripts/record_fallback_clips.sh rtsp://mediamtx:8554 1 2
#
# Uses a local ffmpeg when present; otherwise runs ffmpeg inside the sentinel-anpr image
# (build it first: docker build -f anpr/Dockerfile -t sentinel-anpr .). Clips are served by
# MediaMTX as fallback_<id> when the sandbox is down (CONTRACT.md section 8.1).
set -euo pipefail

if [ $# -lt 2 ]; then
  echo "usage: $0 <rtsp base, e.g. rtsp://host:8554> <camera id> [<camera id> ...]" >&2
  exit 2
fi

BASE="${1%/}"; shift
DURATION="${DURATION:-600}"
OUT="${OUT:-media/fallback}"
PATH_TEMPLATE="${PATH_TEMPLATE:-stream/%s}"   # organiser layout: rtsp://host:8554/stream/<id>
IMAGE="${ANPR_IMAGE:-sentinel-anpr}"
mkdir -p "$OUT"

record() {
  local id="$1" url dest
  url="$BASE/$(printf "$PATH_TEMPLATE" "$id")"
  dest="$OUT/$id.mp4"
  echo "recording $url -> $dest (${DURATION}s, stream copy)"
  if command -v ffmpeg >/dev/null 2>&1; then
    ffmpeg -y -nostdin -hide_banner -loglevel warning -rtsp_transport tcp -timeout 15000000 \
      -i "$url" -t "$DURATION" -c copy -movflags +faststart "$dest"
  else
    docker run --rm --network host -v "$(pwd)/$OUT:/out" "$IMAGE" \
      ffmpeg -y -nostdin -hide_banner -loglevel warning -rtsp_transport tcp -timeout 15000000 \
      -i "$url" -t "$DURATION" -c copy -movflags +faststart "/out/$id.mp4"
  fi
}

status=0
for id in "$@"; do
  record "$id" &
done
for job in $(jobs -p); do
  wait "$job" || status=1
done
ls -la "$OUT"
exit $status
