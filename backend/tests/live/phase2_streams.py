"""Streaming-dependent checks: needs an RTSP publisher on stream/1 and a reader on cam_<mock1> (run inside sentinel-api)."""
import asyncio, json, os, sys, time, hashlib
from datetime import datetime, timedelta, timezone

import httpx

H = os.environ.get("H", "http://localhost:8000")
results = []


def check(name, ok, info=""):
    results.append((name, bool(ok), info))
    print(("PASS " if ok else "FAIL ") + name + (f"  -> {info}" if info else ""), flush=True)


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


async def main():
    async with httpx.AsyncClient(base_url=H, timeout=60) as c:
        r = await c.post("/api/auth/login", json={"username": "jury_admin", "password": "Sentinel@Admin2026"})
        tok = r.json()["access_token"]; A = {"Authorization": f"Bearer {tok}"}; c.cookies.clear()
        cam = (await c.get("/api/cameras", params={"source": "sandbox", "q": "Sachivalaya Gate", "page_size": 1}, headers=A)).json()["items"][0]
        cid = cam["id"]
        # 1. wait for online transition
        t0 = time.time(); status = None
        while time.time() - t0 < 200:
            status = (await c.get(f"/api/cameras/{cid}", headers=A)).json()["status"]
            if status == "online":
                break
            await asyncio.sleep(5)
        check("camera online via probe", status == "online", f"{status} after {time.time()-t0:.0f}s")
        r = await c.get(f"/api/cameras/{cid}/health", headers=A); j = r.json()
        check("health log + transitions", r.status_code == 200 and j["checks"] >= 1 and any(t["to"] == "online" for t in j["transitions"]) and j["log"][0]["source_flag"] in ("probe", "mediamtx"), json.dumps(j["transitions"])[:200] + " " + json.dumps(j["log"][:1])[:150])
        r = await c.get("/api/alerts", params={"type": "camera_offline", "camera_id": cid, "status": "closed"}, headers=A); items = r.json()["items"]
        check("camera_offline alert auto-closed", items and items[0]["note"] == "Camera back online" and items[0]["outcome"] == "resolved" and items[0]["closed_by"] is None, json.dumps(items[:1])[:200])
        r = await c.get("/api/audit", params={"action": "alert.auto_close"}, headers=A); check("audit alert.auto_close (system)", r.json()["total"] >= 1 and r.json()["items"][0]["actor"] == "system", json.dumps(r.json()["items"][:1])[:150])
        r = await c.get("/api/events", params={"camera_id": cid, "type": "camera_online,camera_offline"}, headers=A); check("camera events", r.json()["total"] >= 2)
        r = await c.get(f"/api/streams/{cid}", headers=A); check("stream ready+readers", r.json()["ready"] is True and r.json()["readers"] >= 1, json.dumps(r.json())[:200])
        # 2. first stream timing
        r = await c.post("/api/cameras/import/sandbox", json={"measure_first_stream": True}, headers=A); j = r.json()
        check("first_stream_ready_ms measured", r.status_code == 200 and j["first_stream_ready_ms"] is not None and j["first_stream_ready_ms"] < 15000 and j["unchanged"] == 50, json.dumps({k: j[k] for k in ("added", "updated", "unchanged", "first_stream_ready_ms", "duration_ms")}))
        # 3. recordings
        t0 = time.time(); segs = []
        while time.time() - t0 < 200:
            segs = (await c.get(f"/api/recordings/{cid}", headers=A)).json()["segments"]
            if segs:
                break
            await asyncio.sleep(10)
        check("recordings segments", len(segs) >= 1, f"{len(segs)} after {time.time()-t0:.0f}s " + json.dumps(segs[:1]))
        if segs:
            start = datetime.fromisoformat(segs[0]["start"].replace("Z", "+00:00")) + timedelta(seconds=5)
            r = await c.get(f"/api/recordings/{cid}/play", params={"at": iso(start + timedelta(seconds=10)), "before_s": 10, "duration_s": 20}, headers=A); j = r.json()
            check("recordings play available", r.status_code == 200 and j["available"] is True and j["url"].startswith("/playback/get?path=cam_"), r.text[:200])
            r = await c.post("/api/clips", json={"camera_id": cid, "start_at": iso(start), "duration_s": 20}, headers=A); j = r.json()
            check("clip created", r.status_code == 201 and j["size_bytes"] > 0 and len(j["sha256"]) == 64 and j["url"] == f"/media/clips/{cid}/{j['id']}.mp4", r.text[:250])
            if r.status_code == 201:
                rr = await c.get(j["url"], headers=A, headers_extra=None) if False else await c.get(j["url"], headers={**A, "Range": "bytes=0-99"})
                check("clip range request 206", rr.status_code == 206 and len(rr.content) == 100 and rr.headers.get("content-range", "").startswith("bytes 0-99/") and rr.headers.get("x-sentinel-sha256") == j["sha256"], f"{rr.status_code} {rr.headers.get('content-range')}")
                full = await c.get(j["url"], headers=A); check("clip full download hash", full.status_code == 200 and hashlib.sha256(full.content).hexdigest() == j["sha256"], str(len(full.content)))
                r = await c.get("/api/evidence/verify", params={"path": j["path"]}, headers=A); check("clip verify true", r.json()["match"] is True and r.json()["entity"] == "clip", r.text[:200])
                with open(f"/data/{j['path']}", "ab") as fh:
                    fh.write(b"x")
                r = await c.get("/api/evidence/verify", params={"path": j["path"]}, headers=A); check("clip verify false after corruption", r.json()["match"] is False)
                r = await c.get(f"/api/clips/{j['id']}", headers=A); check("clip get", r.status_code == 200 and r.json()["exists"] is True)
        # 4. health ws stats
        import websockets
        got = {}
        async with websockets.connect(H.replace("http", "ws") + f"/ws/health?token={tok}") as ws:
            got["hello"] = json.loads(await ws.recv())
            deadline = time.time() + 25
            while time.time() < deadline and "stats" not in got:
                try:
                    m = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                    got[m["type"]] = m
                except asyncio.TimeoutError:
                    pass
        check("ws health stats", got.get("hello", {}).get("type") == "hello" and "stats" in got and "cameras" in got["stats"]["data"], json.dumps(got.get("stats", {}))[:200])
        async with websockets.connect(H.replace("http", "ws") + f"/ws/reads/{cid}?token={tok}") as ws:
            m = json.loads(await ws.recv()); check("ws reads hello", m["type"] == "hello" and m["data"]["camera_id"] == cid)
        try:
            async with websockets.connect(H.replace("http", "ws") + "/ws/alerts?token=bad") as ws:
                await ws.recv()
            check("ws bad token closes 4401", False)
        except websockets.exceptions.ConnectionClosed as exc:
            check("ws bad token closes 4401", exc.code == 4401, str(exc.code))
        # 5. retention dry run
        r = await c.get("/api/healthz"); check("api/healthz alias", r.status_code == 200)
    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed: {failed}")
    sys.exit(1 if failed else 0)


asyncio.run(main())
