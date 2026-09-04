# MediaMTX relay with an NVENC-capable ffmpeg for the GPU VM (compose profile
# "gpu" via deploy/docker-compose.gpu.yml).
#
# Layers:
#   stage "mtx": the pinned MediaMTX release (same binary as the CPU stack)
#   runtime:     jrottenberg/ffmpeg:6.1-nvidia2204 (Ubuntu 22.04 + ffmpeg 6.1
#                built with --enable-nvenc/--enable-cuda; LGPL/GPL build,
#                recorded in docs/LICENCES.md)
# The container needs the NVIDIA runtime with NVIDIA_DRIVER_CAPABILITIES
# including "video" for h264_nvenc and -hwaccel cuda to work.

FROM bluenviron/mediamtx:1.20.1-ffmpeg AS mtx

FROM jrottenberg/ffmpeg:6.1-nvidia2204

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates wget tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY --from=mtx /mediamtx /mediamtx
COPY --from=mtx /mediamtx.yml /mediamtx.reference.yml

# Sanity check at build time: the ffmpeg in this image must expose NVENC.
RUN ffmpeg -hide_banner -encoders 2>/dev/null | grep -q h264_nvenc

ENV NVIDIA_DRIVER_CAPABILITIES=compute,video,utility
WORKDIR /
EXPOSE 8554 8888 8889 9996 9997 8189/udp 8189/tcp
ENTRYPOINT ["/mediamtx"]
CMD ["/mediamtx.yml"]
