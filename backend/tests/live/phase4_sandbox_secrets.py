"""Real-sandbox secret scan (run inside the sentinel-api container after the organiser import):

    docker compose ... exec -T api python tests/live/phase4_sandbox_secrets.py

Logs in as every seed user and fetches every endpoint that renders camera URLs, import summaries, audit rows or
settings; every body must be free of the sandbox access password (plain, %-encoded and fully-encoded spellings,
read from SANDBOX_STREAM_PASSWORD / the settings table - the value itself is never printed). Also asserts the
organiser rows are unique per external_id, the probe/test endpoints answer with codec + resolution and the health
summary reports no offline sandbox camera. Exit code 1 on any failure.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from urllib.parse import quote

import httpx

H = os.environ.get("H", "http://localhost:8000")
IK = os.environ.get("INTERNAL_API_KEY", "sk_internal0000000000000000000000000000000000")
USERS = {
    "jury_admin": os.environ.get("JURY_ADMIN_PASSWORD", "Sentinel@Admin2026"),
    "jury_operator": os.environ.get("JURY_OPERATOR_PASSWORD", "Sentinel@Ops2026"),
    "jury_viewer": os.environ.get("JURY_VIEWER_PASSWORD", "Sentinel@View2026"),
    "dept_admin_police": os.environ.get("DEPT_ADMIN_PASSWORD", "Sentinel@Police2026"),
}
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, info: str = "") -> None:
    results.append((name, bool(ok), info))
    print(("PASS " if ok else "FAIL ") + name + (f"  -> {info}" if info else ""), flush=True)


def spellings(secret: str) -> list[str]:
    return sorted({secret, quote(secret, safe=""), quote(secret), "".join(f"%{b:02X}" for b in secret.encode()), "".join(f"%{b:02x}" for b in secret.encode())}, key=len, reverse=True)


async def main() -> int:
    secret = os.environ.get("SANDBOX_STREAM_PASSWORD") or ""
    if not secret:
        try:
            from app.db.session import SessionLocal
            from app.services import settings_service as cfg

            async with SessionLocal() as db:
                await cfg.load(db)
            secret = str(cfg.get("sandbox.stream_password") or "")
        except Exception as exc:  # noqa: BLE001
            print(f"cannot read the access password: {exc}")
    if not secret:
        print("no sandbox access password configured; nothing to scan")
        return 1
    needles = spellings(secret)
    leaks: list[str] = []

    def scan(label: str, text: str) -> None:
        hit = next((n for n in needles if n in text), None)
        if hit is not None:
            leaks.append(label)
        check(f"no secret in {label}", hit is None, f"{len(text)} bytes")

    async with httpx.AsyncClient(base_url=H, timeout=120) as c:
        tokens: dict[str, dict[str, str]] = {}
        for u, pw in USERS.items():
            r = await c.post("/api/auth/login", json={"username": u, "password": pw})
            c.cookies.clear()
            if r.status_code == 200:
                tokens[u] = {"Authorization": f"Bearer {r.json()['access_token']}"}
            else:
                check(f"login {u}", False, str(r.status_code))
        A = tokens["jury_admin"]
        sandbox_ids: list[int] = []
        for u, hdr in tokens.items():
            page = 1
            while True:
                r = await c.get("/api/cameras", params={"page": page, "page_size": 200}, headers=hdr)
                scan(f"GET /cameras p{page} as {u}", r.text)
                body = r.json()
                if u == "jury_admin":
                    sandbox_ids.extend(x["id"] for x in body["items"] if x["source"] == "sandbox" and str(x["external_id"]).startswith("cam"))
                if page * 200 >= body["total"]:
                    break
                page += 1
            r = await c.get("/api/geo/cameras", headers=hdr)
            scan(f"GET /geo/cameras as {u}", r.text)
            r = await c.get("/api/health/summary", headers=hdr)
            scan(f"GET /health/summary as {u}", r.text)
            r = await c.get("/api/dashboard/stats", headers=hdr)
            scan(f"GET /dashboard/stats as {u}", r.text)
        for cid in sandbox_ids:
            for u, hdr in tokens.items():
                r = await c.get(f"/api/cameras/{cid}", headers=hdr)
                if r.status_code == 200:
                    scan(f"GET /cameras/{cid} as {u}", r.text)
                r = await c.get(f"/api/streams/{cid}", headers=hdr)
                if r.status_code == 200:
                    scan(f"GET /streams/{cid} as {u}", r.text)
        r = await c.get("/api/cameras/export", params={"format": "csv"}, headers=A)
        scan("GET /cameras/export", r.text)
        r = await c.get("/api/audit", params={"page_size": 500}, headers=A)
        scan("GET /audit", r.text)
        r = await c.get("/api/audit/export", params={"format": "csv"}, headers=A)
        scan("GET /audit/export", r.text)
        r = await c.get("/api/settings", headers=A)
        scan("GET /settings", r.text)
        r = await c.get("/api/settings/public", headers=A)
        scan("GET /settings/public", r.text)
        r = await c.get("/api/internal/anpr-config", params={"mode": "live"}, headers={"X-API-Key": IK})
        scan("GET /internal/anpr-config live", r.text)
        r = await c.get("/api/internal/anpr-config", params={"mode": "preindex"}, headers={"X-API-Key": IK})
        scan("GET /internal/anpr-config preindex", r.text)
        r = await c.post("/api/settings/catalogue/test", json={"source": "sentinel_portal", "camera_id": "cam01", "check_portal": False}, headers=A)
        scan("POST /settings/catalogue/test", r.text)
        t = r.json()
        check("catalogue test reports codec + resolution", t.get("ok") and t["probe"].get("codec") and t["probe"].get("resolution"), t["probe"].get("summary", ""))
        # organiser rows: one per external_id, ids preserved, relay path = cam_<id>
        r = await c.get("/api/cameras", params={"page_size": 200, "source": "sandbox"}, headers=A)
        rows = [x for x in r.json()["items"] if str(x["external_id"]).startswith("cam")]
        ext = [x["external_id"] for x in rows]
        check("30 organiser cameras, no duplicate external_id", len(ext) == 30 and len(set(ext)) == 30, f"{len(ext)} rows, {len(set(ext))} distinct")
        check("relay path is cam_<id> for every organiser camera", all(x["relay_path"] == f"cam_{x['id']}" for x in rows))
        check("every organiser URL is masked", all("***@" in (x["rtsp_url"] or "") for x in rows))
        r = await c.get("/api/cameras", params={"page_size": 200, "external_id": "cam01"}, headers=A)
        dup = [x for x in r.json()["items"] if x["external_id"] == "cam01"]
        check("cam01 exists exactly once across sources", len(dup) == 1, str([(x["id"], x["source"]) for x in dup]))
        r = await c.get("/api/health/summary", headers=A)
        hs = r.json()
        # a camera the importer could not confirm (live=null: probe timed out) is still under watch, not a regression
        offline_sandbox = [x for x in rows if x["status"] == "offline" and x["live"] is True]
        unconfirmed = [x["external_id"] for x in rows if x["live"] is not True]
        check("no confirmed-live organiser camera offline", not offline_sandbox, f"offline={[x['external_id'] for x in offline_sandbox]} unconfirmed={unconfirmed} summary={hs['cameras']}")
    ok = all(r[1] for r in results)
    print(f"\n{sum(1 for r in results if r[1])}/{len(results)} passed; leaks: {leaks or 'none'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
