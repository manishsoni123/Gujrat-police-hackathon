"""Offline → camera_offline alert → back online → auto-close (CONTRACT §5.6 step 5, check 11 semantics)."""
import asyncio, json, os, sys, time

import httpx
import websockets

H = os.environ.get("H", "http://localhost:8000")
results = []


def check(name, ok, info=""):
    results.append((name, bool(ok), info))
    print(("PASS " if ok else "FAIL ") + name + (f"  -> {info}" if info else ""), flush=True)


async def main():
    async with httpx.AsyncClient(base_url=H, timeout=60) as c:
        tok = (await c.post("/api/auth/login", json={"username": "jury_admin", "password": "Sentinel@Admin2026"})).json()["access_token"]
        A = {"Authorization": f"Bearer {tok}"}; c.cookies.clear()
        cam = (await c.get("/api/cameras", params={"source": "sandbox", "q": "Sachivalaya Gate", "page_size": 1}, headers=A)).json()["items"][0]
        cid = cam["id"]
        health_msgs = []

        async def listen():
            async with websockets.connect(H.replace("http", "ws") + f"/ws/health?token={tok}") as ws:
                await ws.recv()
                deadline = time.time() + 420
                while time.time() < deadline:
                    await ws.send(json.dumps({"type": "ping"}))  # §9: clients ping every 25 s; idle sockets close after 90 s
                    try:
                        m = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                    except asyncio.TimeoutError:
                        continue
                    if m["type"] == "health" and m["data"]["camera_id"] == cid:
                        health_msgs.append(m["data"])
                        if m["data"]["status"] == "online" and any(h["status"] == "offline" for h in health_msgs):
                            return
        task = asyncio.create_task(listen())
        r = await c.put(f"/api/cameras/{cid}", json={"rtsp_url": "rtsp://mediamtx:8554/stream/99"}, headers=A)
        check("point camera at dead source", r.status_code == 200 and r.json()["rtsp_url"].endswith("/stream/99"), r.text[:120])
        t0 = time.time(); status = None
        while time.time() - t0 < 240:
            status = (await c.get(f"/api/cameras/{cid}", headers=A)).json()["status"]
            if status == "offline":
                break
            await asyncio.sleep(5)
        check("camera offline after 3 failed checks", status == "offline", f"{status} after {time.time()-t0:.0f}s")
        r = await c.get("/api/alerts", params={"type": "camera_offline", "camera_id": cid, "status": "new"}, headers=A); items = r.json()["items"]
        check("camera_offline alert new/low", items and items[0]["priority"] == "low" and items[0]["watchlist"] is None and items[0]["snapshot_url"], json.dumps(items[:1])[:200])
        r = await c.put(f"/api/cameras/{cid}", json={"rtsp_url": "rtsp://mediamtx:8554/stream/1"}, headers=A)
        check("restore source", r.status_code == 200)
        t0 = time.time(); status = None
        while time.time() - t0 < 150:
            status = (await c.get(f"/api/cameras/{cid}", headers=A)).json()["status"]
            if status in ("online", "degraded"):
                break
            await asyncio.sleep(5)
        check("camera back online", status in ("online", "degraded"), f"{status} after {time.time()-t0:.0f}s")
        r = await c.get("/api/alerts", params={"type": "camera_offline", "camera_id": cid, "status": "closed"}, headers=A); items = r.json()["items"]
        check("camera_offline alert auto-closed", items and items[0]["note"] == "Camera back online" and items[0]["outcome"] == "resolved" and items[0]["closed_by"] is None and items[0]["acknowledged_at"], json.dumps(items[:1])[:220])
        r = await c.get("/api/audit", params={"action": "alert.auto_close"}, headers=A); check("audit alert.auto_close by system", r.json()["total"] >= 1 and r.json()["items"][0]["actor"] == "system" and r.json()["items"][0]["role"] == "system", json.dumps(r.json()["items"][:1])[:150])
        r = await c.get("/api/events", params={"camera_id": cid, "type": "camera_online,camera_offline"}, headers=A); check("camera offline/online events", r.json()["total"] >= 2, str(r.json()["total"]))
        r = await c.get("/api/audit", params={"action": "camera.update", "entity_id": str(cid)}, headers=A); it = r.json()["items"][0]
        check("camera.update audit diff", "rtsp_url" in (it["before"] or {}) and "rtsp_url" in (it["after"] or {}), json.dumps(it)[:200])
        try:
            await asyncio.wait_for(task, timeout=30)
        except asyncio.TimeoutError:
            task.cancel()
        check("ws health messages offline+online", any(h["status"] == "offline" for h in health_msgs) and any(h["status"] == "online" and h["previous_status"] == "offline" for h in health_msgs), json.dumps([(h["status"], h["previous_status"], h["source_flag"]) for h in health_msgs]))
        r = await c.get("/api/health/summary", headers=A); j = r.json()
        check("health summary online>=8 not_streaming==42 offline==0", j["cameras"]["online"] >= 8 and j["cameras"]["not_streaming"] == 42 and j["cameras"]["offline"] == 0, json.dumps(j["cameras"]) + f" uptime={j['uptime_24h_pct']} mtx={j['mediamtx']}")
    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed: {failed}")
    sys.exit(1 if failed else 0)


asyncio.run(main())
