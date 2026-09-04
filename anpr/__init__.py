"""Sentinel Gujarat ANPR / analytics worker (Dynatech Consultancy).

Package layout (see anpr/README.md):
    pipeline.py   decoder pool + single inference loop + batching/POST to the API
    decode.py     ffmpeg subprocess wrapper (RTSP over TCP, file sources for tests)
    detector.py   plate detectors: ONNX YOLO-v9-t (open-image-models) and a contour fallback
    ocr.py        PaddleOCR PP-OCRv4 wrapper (det + rec, two-line plates)
    normalise.py  plate normalisation (CONTRACT.md section 3.4, shared with the backend)
    voting.py     char-wise majority voting per camera / plate bucket
    sightings.py  sighting open / extend / close state machine
    objects.py    YOLOX-s COCO counting, centroid tracker, intrusion zones
    client.py     API client (X-API-Key), retry/backoff, dry-run mode
    config.py     settings from config.yml / environment / CLI / server
"""

__version__ = "1.0.0-phase1"
