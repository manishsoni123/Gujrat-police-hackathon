# Live-stack integration scripts (not collected by pytest)

Run inside the `api` container against a running stack (they seed the registry from the mock catalogue if empty):

```
docker compose -f deploy/docker-compose.yml --project-directory . exec api python tests/live/phase1_api.py
docker compose -f deploy/docker-compose.yml --project-directory . exec api python tests/live/phase2_streams.py
docker compose -f deploy/docker-compose.yml --project-directory . exec api python tests/live/phase3_offline_online.py
```

* `phase1_api.py` – ANPR ingestion → alert → WebSocket → search/route/PDF → reports → evidence → settings → webhooks → RBAC → audit (127 checks).
* `phase2_streams.py` – needs live relay streams: online transition, first-stream timing, recordings, clips, range requests, WS health.
* `phase3_offline_online.py` – points camera 1 at a dead source, waits for `offline` + `camera_offline` alert, restores it and asserts the auto-close.

Phase 1 ends with the login rate-limit check, so wait 60 s before running phase 2 from the same host. `H` overrides the base URL.
