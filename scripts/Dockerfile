# Sentinel Gujarat - tools image (CONTRACT §1.1 "synth", compose profile "tools").
# Runs scripts/make_synthetic_videos.py: OpenCV + numpy + Pillow frames encoded by
# ffmpeg (libx264 for cam_1..7, libx265 for cam_8) into /media/synthetic.
#
#   docker compose -f deploy/docker-compose.yml --project-directory . --profile tools run --rm synth
#
# Build context is the repository root.

FROM python:3.11-slim

LABEL org.opencontainers.image.title="Sentinel Gujarat tools" \
      org.opencontainers.image.vendor="Dynatech Consultancy"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    SYNTH_OUT=/media/synthetic

# ffmpeg (Debian build with libx264 + libx265) and the DejaVu font used for plate text.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
      "opencv-python-headless>=4.9,<5" \
      "numpy>=1.26,<3" \
      "pillow>=10.3,<12"

WORKDIR /app
COPY scripts/ /app/scripts/

# /media is the bind mount of ./media (read-write for this image only).
VOLUME ["/media"]
# No ENTRYPOINT: docker-compose.yml passes the full command ("python scripts/make_synthetic_videos.py"),
# and "docker run --rm sentinel-synth python -c ..." keeps working for ad-hoc checks.
CMD ["python", "scripts/make_synthetic_videos.py"]
