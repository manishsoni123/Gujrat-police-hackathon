# syntax=docker/dockerfile:1.6
# Sentinel Gujarat - ANPR / analytics worker image.
#
# Build (context = repository root):
#   docker build -f anpr/Dockerfile -t sentinel-anpr .                       # CPU image (laptop, default)
#   docker build -f anpr/Dockerfile --build-arg ANPR_BASE=gpu -t sentinel-anpr:gpu .   # CUDA image (VM)
#
# ANPR_BASE=cpu -> python:3.11-slim-bookworm (Debian 12: paddlepaddle 2.6 segfaults on trixie zlib) + Debian ffmpeg 5.1 + onnxruntime (CPU) + PaddlePaddle CPU wheel
# ANPR_BASE=gpu -> pytorch CUDA 12.1 runtime + NVIDIA-enabled ffmpeg (jrottenberg) + onnxruntime-gpu
# Both stages install the same anpr/requirements.txt; only the onnxruntime wheel differs.
# CPU=1 at run time forces software decode and the CPU execution provider even in the GPU image.

ARG ANPR_BASE=cpu

# ---------------------------------------------------------------------------
# CPU base
# ---------------------------------------------------------------------------
FROM python:3.11-slim-bookworm AS base-cpu
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg fonts-dejavu-core curl ca-certificates libgomp1 \
    && rm -rf /var/lib/apt/lists/*
ENV CPU=1 \
    ONNXRUNTIME_PACKAGE=onnxruntime==1.19.2

# ---------------------------------------------------------------------------
# GPU base (not testable on the laptop; used on the CUDA VM with COMPOSE_PROFILES=gpu)
# ---------------------------------------------------------------------------
FROM jrottenberg/ffmpeg:6.1-nvidia2204 AS ffmpeg-nvidia

FROM pytorch/pytorch:2.4.1-cuda12.1-cudnn9-runtime AS base-gpu
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        fonts-dejavu-core curl ca-certificates libgomp1 libva2 libva-drm2 libvdpau1 \
    && rm -rf /var/lib/apt/lists/*
# NVIDIA-enabled ffmpeg/ffprobe (NVDEC/NVENC) copied from the jrottenberg build.
COPY --from=ffmpeg-nvidia /usr/local/bin/ffmpeg /usr/local/bin/ffprobe /usr/local/bin/
COPY --from=ffmpeg-nvidia /usr/local/lib /usr/local/lib
ENV LD_LIBRARY_PATH=/usr/local/lib:/usr/local/cuda/lib64:${LD_LIBRARY_PATH} \
    NVIDIA_DRIVER_CAPABILITIES=compute,video,utility \
    CPU=0 \
    ONNXRUNTIME_PACKAGE=onnxruntime-gpu==1.19.2

# ---------------------------------------------------------------------------
# Common runtime layer
# ---------------------------------------------------------------------------
FROM base-${ANPR_BASE} AS runtime
ARG ANPR_BASE
ARG DOWNLOAD_WEIGHTS=1
ARG PADDLE_MODELS=1

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    OMP_NUM_THREADS=2 \
    FLAGS_use_mkldnn=0 \
    ANPR_BASE=${ANPR_BASE}

WORKDIR /app

# Python dependencies. paddleocr pulls opencv-python + opencv-contrib-python (GUI builds needing libGL);
# they are replaced by the headless wheel so the slim image needs no X11/GL libraries.
COPY anpr/requirements.txt /app/anpr/requirements.txt
RUN python -m pip install --upgrade pip==24.2 \
    && python -m pip install --timeout 180 --retries 8 -r /app/anpr/requirements.txt "${ONNXRUNTIME_PACKAGE}" \
    && python -m pip uninstall -y opencv-python opencv-contrib-python opencv-python-headless \
    && python -m pip install --timeout 180 --retries 8 --no-deps opencv-python-headless==4.10.0.84 \
    && python -c "import cv2, onnxruntime, paddle, paddleocr, fast_plate_ocr; print('cv2', cv2.__version__, 'ort', onnxruntime.__version__, 'paddle', paddle.__version__, 'fast_plate_ocr ok')"

# The worker runs as an unprivileged user (uid 10001, like the api image's "sentinel" user): it
# decodes untrusted RTSP bitstreams with ffmpeg/OpenCV/Paddle and carries INTERNAL_API_KEY, so a
# decoder bug must not yield root. HOME=/app makes PaddleOCR (~/.paddleocr) and Paddle (~/.paddle,
# ~/.cache) resolve under /app, which the user owns; nothing else is written at run time
# (PYTHONDONTWRITEBYTECODE, no __pycache__), so the container also works with `read_only: true`
# plus a tmpfs on /tmp (deploy/docker-compose.yml).
ENV HOME=/app
RUN groupadd -r -g 10001 anpr && useradd -r -u 10001 -g anpr -d /app -s /usr/sbin/nologin anpr

# paddlepaddle 2.6 exports its own (older) zlib symbols from libpaddle.so, which it loads with
# RTLD_GLOBAL. With lazy binding the system libz.so.1 then resolves its *internal* calls
# (inflateInit2_ -> inflateReset2) to Paddle's copy and every gzip/tar operation after
# "import paddle" segfaults ("C++ Traceback: inflateReset2"). LD_BIND_NOW=1 binds every library
# to itself at load time, which removes the clash; anpr/pipeline.py re-execs itself with this
# variable set when it is missing (see anpr/README.md, "Known issues").
ENV LD_BIND_NOW=1

# PaddleOCR English models (PP-OCRv3 detection - PaddleOCR ships no v4 English det model - and
# PP-OCRv4 recognition), fetched once at build time so the worker never downloads at run time
# (lands in $HOME/.paddleocr/whl/... = /app/.paddleocr, handed to the anpr user below).
# "import zlib" must precede the Paddle import (same zlib clash: the zlib extension has to bind
# to libz.so.1 before libpaddle.so enters the global symbol scope).
RUN if [ "${PADDLE_MODELS}" = "1" ]; then \
        python -c "import zlib; from paddleocr import PaddleOCR; PaddleOCR(lang='en', use_angle_cls=False, show_log=False); print('paddleocr models ready')"; \
    fi

# Detector weights (ONNX) and the fast-plate-ocr recogniser models (+ their .yaml configs); the SHA-256 ledger is anpr/weights/HASHES.txt. The build FAILS when a
# weight is missing or its hash is wrong - a green image without the plate detector would silently
# run the contour fallback on real footage. Files already present in the build context are
# verified; missing ones are downloaded from the upstream GitHub releases.
#   DOWNLOAD_WEIGHTS=1 (default)  download what is missing, verify, fail on any error
#   DOWNLOAD_WEIGHTS=0            offline build: the .onnx files must be pre-placed in anpr/weights/;
#                                 verified, fail when missing or mismatched
#   DOWNLOAD_WEIGHTS=none         deliberately build a contour-only image (unit tests / offline CI);
#                                 the worker then starts DEGRADED and says so in the heartbeat
COPY --chown=anpr:anpr anpr/weights/ /app/weights/
RUN case "${DOWNLOAD_WEIGHTS}" in \
        1)    python /app/weights/download.py --dir /app/weights --require ;; \
        0)    python /app/weights/download.py --dir /app/weights --no-download --require ;; \
        none) echo "DOWNLOAD_WEIGHTS=none: building WITHOUT ONNX weights (contour-only, degraded)"; \
              python /app/weights/download.py --dir /app/weights --no-download || true ;; \
        *)    echo "DOWNLOAD_WEIGHTS must be 1, 0 or none (got: ${DOWNLOAD_WEIGHTS})" >&2; exit 1 ;; \
    esac

# Worker code and helper scripts.
COPY --chown=anpr:anpr anpr/ /app/anpr/
COPY --chown=anpr:anpr scripts/ /app/scripts/
RUN rm -rf /app/anpr/weights && ln -s /app/weights /app/anpr/weights \
    && chown -R anpr:anpr /app

ENV PYTHONPATH=/app \
    ANPR_MODE=live \
    API_URL=http://api:8000 \
    ANPR_CONFIG=/app/anpr/config.yml \
    ANPR_DET_MODEL=/app/weights/yolo-v9-t-384-license-plates-end2end.onnx \
    OBJECT_MODEL=/app/weights/yolox_s.onnx \
    LOG_LEVEL=INFO

# Drop privileges: everything from here on (pipeline, pytest, eval tools) runs as uid 10001.
USER anpr

# The worker has no listening port; liveness is the heartbeat it posts to the API.
CMD ["python", "-m", "anpr.pipeline"]
