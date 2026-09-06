"""End-to-end checks against a running API (run inside the sentinel-api container)."""
import asyncio, io, json, os, sys, time, hashlib
from datetime import datetime, timedelta, timezone

import httpx
from PIL import Image, ImageDraw

H = os.environ.get("H", "http://localhost:8000")
IK = "sk_internal0000000000000000000000000000000000"
results = []


def check(name, ok, info=""):
    results.append((name, bool(ok), info))
    print(("PASS " if ok else "FAIL ") + name + (f"  -> {info}" if info else ""), flush=True)


def jpeg(w=240, h=80, text="X"):
    img = Image.new("RGB", (w, h), "white")
    ImageDraw.Draw(img).text((10, 30), text, fill="black")
    b = io.BytesIO()
    img.save(b, format="JPEG", quality=70)
    return b.getvalue()


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


async def main():
    async with httpx.AsyncClient(base_url=H, timeout=60) as c:
        r = await c.post("/api/auth/login", json={"username": "jury_admin", "password": "Sentinel@Admin2026"})
        tok = r.json()["access_token"]; A = {"Authorization": f"Bearer {tok}"}
        c.cookies.clear()
        r = await c.post("/api/auth/login", json={"username": "jury_operator", "password": "Sentinel@Ops2026"}); OT = {"Authorization": f"Bearer {r.json()['access_token']}"}
        r = await c.post("/api/auth/login", json={"username": "jury_viewer", "password": "Sentinel@View2026"}); VT = {"Authorization": f"Bearer {r.json()['access_token']}"}
        if (await c.get("/api/cameras", params={"source": "sandbox", "page_size": 1}, headers=A)).json()["total"] == 0:
            r = await c.post("/api/cameras/import/sandbox", json={"measure_first_stream": False}, headers=A); check("seed sandbox import", r.status_code == 200 and r.json()["added"] == 50 and r.json()["relay_paths_created"] == 61, json.dumps(r.json())[:200])
            with open("/app/seeds/cameras_sample.csv", "rb") as fh:
                r = await c.post("/api/cameras/import/csv", files={"file": ("cameras_sample.csv", fh.read(), "text/csv")}, headers=A)
            check("seed csv import", r.status_code == 200 and r.json()["added"] == 8, json.dumps(r.json())[:200])
            r = await c.post("/api/v1/cameras/bulk", json={"cameras": [{"external_id": "GSRTC-101", "name": "Mehsana Depot Gate", "department_code": "GSRTC", "lat": 23.588, "lon": 72.369, "district": "Mehsana"}]}, headers={"X-API-Key": "sk_bulk00000000000000000000000000000000000000"})
            check("seed bulk import", r.status_code == 200 and r.json()["added"] == 1)
        cams = (await c.get("/api/cameras", params={"source": "sandbox", "page_size": 100, "sort": "external_id"}, headers=A)).json()["items"]
        by_ext = {x["external_id"]: x for x in cams}
        c1, c3, c6, c2 = by_ext["1"], by_ext["3"], by_ext["6"], by_ext["2"]
        check("anpr-config live", (r := await c.get("/api/internal/anpr-config", params={"mode": "live"}, headers={"X-API-Key": IK})).status_code == 200 and len(r.json()["cameras"]) == 8 and r.json()["cameras"][0]["rtsp_url"].startswith("rtsp://"), str(r.status_code) + " " + str(len(r.json().get("cameras", []))))
        r = await c.get("/api/internal/anpr-config", params={"mode": "preindex"}, headers={"X-API-Key": IK}); check("anpr-config preindex modes", r.status_code == 200 and {x["mode"] for x in r.json()["cameras"]} <= {"both", "preindex"}, str({x["mode"] for x in r.json()["cameras"]}))
        check("internal wrong key 401", (await c.get("/api/internal/anpr-config", headers={"X-API-Key": "sk_" + "a" * 40})).status_code == 401)
        check("internal bulk key 403", (await c.get("/api/internal/anpr-config", headers={"X-API-Key": "sk_bulk00000000000000000000000000000000000000"})).status_code == 403)

        # heartbeat
        r = await c.post("/api/internal/heartbeat", headers={"X-API-Key": IK}, json={"worker_id": "live-e2e", "mode": "live", "version": "1.0.0-phase1", "gpu": False, "cpu_flag": True, "started_at": iso(datetime.now(timezone.utc)), "cameras": [{"id": c1["id"], "state": "running", "fps_actual": 4.8, "frames": 100, "reads": 3, "last_frame_at": iso(datetime.now(timezone.utc)), "decoder_restarts": 0, "last_error": None}], "detector": "contour", "object_detect": True})
        check("heartbeat 204", r.status_code == 204, r.text[:100])
        # snapshot
        r = await c.post("/api/internal/snapshots", headers={"X-API-Key": IK}, data={"camera_id": str(c1["id"]), "captured_at": iso(datetime.now(timezone.utc))}, files={"file": ("snap.jpg", jpeg(480, 270, "snap"), "image/jpeg")})
        check("snapshot 204", r.status_code == 204, r.text[:100])
        r = await c.get(f"/api/streams/{c1['id']}", headers=A); check("stream snapshot_url + stale false", r.json()["snapshot_url"] == f"/media/snapshots/cam_{c1['id']}.jpg" and r.json()["snapshot_stale"] is False, json.dumps(r.json())[:200])
        r = await c.get(f"/media/snapshots/cam_{c1['id']}.jpg", headers=A); check("media snapshot 200 no-store", r.status_code == 200 and r.headers.get("cache-control") == "no-store", str(r.status_code) + " " + str(r.headers.get("cache-control")))
        c.cookies.clear(); check("media without token 401", (await c.get(f"/media/snapshots/cam_{c1['id']}.jpg")).status_code == 401)
        check("media cookie auth", (await c.get(f"/media/snapshots/cam_{c1['id']}.jpg", cookies={"sg_session": tok})).status_code == 200)
        check("media traversal 404", (await c.get("/media/crops/../../etc/passwd", headers=A)).status_code == 404)

        # ws alerts listener
        import websockets
        ws_url = H.replace("http", "ws") + f"/ws/alerts?token={tok}"
        got = {}

        async def listen():
            async with websockets.connect(ws_url) as ws:
                hello = json.loads(await ws.recv()); got["hello"] = hello
                await ws.send(json.dumps({"type": "ping"}))
                deadline = time.time() + 20
                while time.time() < deadline:
                    await ws.send(json.dumps({"type": "ping"}))
                    try:
                        m = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                    except asyncio.TimeoutError:
                        continue
                    got.setdefault(m["type"], []).append(m)
                    if "alert" in got and "alert_update" in got:
                        break
        task = asyncio.create_task(listen())
        await asyncio.sleep(1.0)

        # detections batch: GJ01AB1234 (stolen/critical seeded) on camera 1
        now = datetime.now(timezone.utc)
        key = f"{c1['id']}:GJ01AB1234:{int((now - timedelta(seconds=2)).timestamp()*1000)}"
        payload = {"worker_id": "live-e2e", "mode": "live", "camera_id": c1["id"], "camera_external_id": "1", "sent_at": iso(now),
                   "reads": [{"captured_at": iso(now - timedelta(seconds=1)), "stream_pts": 5.2, "frame_index": 52, "plate_raw": "GJ 01 AB 1234", "plate_norm": "GJ01AB1234", "is_valid_format": True, "confidence": 0.93, "bbox": [412, 388, 236, 64], "crop_file": "crop_0", "sighting_key": key},
                             {"captured_at": iso(now - timedelta(seconds=1)), "plate_raw": "GJ 05 RS 9O12", "confidence": 0.85, "bbox": [1, 2, 3, 4], "crop_file": "crop_1", "sighting_key": f"{c1['id']}:GJ05RS9012:{int(now.timestamp()*1000)}"},
                             {"captured_at": iso(now), "plate_raw": "ABC", "confidence": 0.5, "crop_file": "missing_crop", "sighting_key": "x"}],
                   "sightings": [{"key": key, "plate_norm": "GJ01AB1234", "is_valid_format": True, "first_seen": iso(now - timedelta(seconds=2)), "last_seen": iso(now), "read_count": 1, "best_conf": 0.93, "best_read_captured_at": iso(now - timedelta(seconds=1)), "best_crop_file": "crop_0", "frame_file": "frame_0", "closed": False},
                                 {"key": f"{c1['id']}:GJ05RS9012:{int(now.timestamp()*1000)}", "plate_norm": "GJ05RS9012", "is_valid_format": True, "first_seen": iso(now), "last_seen": iso(now), "read_count": 1, "best_conf": 0.85, "best_crop_file": "crop_1", "closed": False}],
                   "object_counts": [{"minute": iso(now.replace(second=0, microsecond=0) - timedelta(minutes=1)), "class": "car", "count": 7}, {"minute": iso(now.replace(second=0, microsecond=0) - timedelta(minutes=1)), "class": "person", "count": 3}],
                   "live_counts": {"minute": iso(now.replace(second=0, microsecond=0)), "counts": {"car": 2, "person": 1}},
                   "events": [{"type": "loop_reset", "occurred_at": iso(now), "note": "pts 90.0 -> 0.1 (discontinuity)", "stream_pts_before": 90.0, "stream_pts_after": 0.1}]}
        files = {"payload": (None, json.dumps(payload)), "crop_0": ("c0.jpg", jpeg(text="GJ01AB1234"), "image/jpeg"), "crop_1": ("c1.jpg", jpeg(text="GJ05RS9012"), "image/jpeg"), "frame_0": ("f0.jpg", jpeg(960, 540, "frame"), "image/jpeg")}
        r = await c.post("/api/internal/detections", headers={"X-API-Key": IK}, files=files)
        j = r.json(); check("detections batch", r.status_code == 200 and j["accepted_reads"] == 2 and j["accepted_sightings"] == 2 and j["alerts_created"] == 2 and j["object_counts_upserted"] == 2 and j["events_created"] == 1 and len(j["rejected"]) == 1, json.dumps(j)[:300])
        # second batch same plate same camera → suppression (alert_update)
        now2 = datetime.now(timezone.utc)
        payload2 = {"worker_id": "live-e2e", "mode": "live", "camera_id": c1["id"], "sent_at": iso(now2), "reads": [{"captured_at": iso(now2), "plate_raw": "GJ01AB1234", "confidence": 0.9, "crop_file": "crop_0", "sighting_key": key}], "sightings": [{"key": key, "plate_norm": "GJ01AB1234", "first_seen": iso(now - timedelta(seconds=2)), "last_seen": iso(now2), "read_count": 2, "best_conf": 0.93, "closed": True}]}
        r = await c.post("/api/internal/detections", headers={"X-API-Key": IK}, files={"payload": (None, json.dumps(payload2)), "crop_0": ("c0.jpg", jpeg(text="GJ01AB1234"), "image/jpeg")})
        j = r.json(); check("suppression attaches read", r.status_code == 200 and j["alerts_updated"] == 1 and j["alerts_created"] == 0, json.dumps(j)[:200])
        # same plate on camera 3 and 6 → route across 3 cameras
        for cam, offset in ((c3, 20), (c6, 45), (c2, 70)):
            t = now - timedelta(seconds=100 - offset)
            k = f"{cam['id']}:GJ01AB1234:{int(t.timestamp()*1000)}"
            p = {"worker_id": "live-e2e", "mode": "live", "camera_id": cam["id"], "sent_at": iso(t), "reads": [{"captured_at": iso(t), "plate_raw": "GJ01AB1234", "confidence": 0.9, "crop_file": "crop_0", "sighting_key": k}], "sightings": [{"key": k, "plate_norm": "GJ01AB1234", "first_seen": iso(t), "last_seen": iso(t + timedelta(seconds=3)), "read_count": 1, "best_conf": 0.9, "best_crop_file": "crop_0", "closed": True}]}
            await c.post("/api/internal/detections", headers={"X-API-Key": IK}, files={"payload": (None, json.dumps(p)), "crop_0": ("c0.jpg", jpeg(text="GJ01AB1234"), "image/jpeg")})
        # fuzzy read on camera 2: GJ01AB1Z34 (distance 1 after norm? normalise fixes Z->2 in digit slot => exact) so use GJ01AB1235 (distance 1)
        t = now - timedelta(seconds=10); k = f"{c2['id']}:GJ01AB1235:{int(t.timestamp()*1000)}"
        p = {"worker_id": "live-e2e", "mode": "live", "camera_id": c2["id"], "sent_at": iso(t), "reads": [{"captured_at": iso(t), "plate_raw": "GJ01AB1235", "confidence": 0.7, "crop_file": "crop_0", "sighting_key": k}], "sightings": [{"key": k, "plate_norm": "GJ01AB1235", "first_seen": iso(t), "last_seen": iso(t), "read_count": 1, "best_conf": 0.7, "best_crop_file": "crop_0", "closed": True}]}
        await c.post("/api/internal/detections", headers={"X-API-Key": IK}, files={"payload": (None, json.dumps(p)), "crop_0": ("c0.jpg", jpeg(text="GJ01AB1235"), "image/jpeg")})
        await asyncio.wait_for(task, timeout=30)
        check("ws hello", got.get("hello", {}).get("type") == "hello" and got["hello"]["data"]["user"] == "jury_admin")
        check("ws pong", "pong" in got)
        al = got.get("alert", [])
        check("ws alert envelope", any(a["data"]["plate_norm"] == "GJ01AB1234" and a["data"]["priority"] == "critical" and a["data"]["confidence_level"] == "exact" and a["data"]["notify_title"].startswith("CRITICAL") and a["data"]["latency_ms"] is not None for a in al), json.dumps([(a["data"]["plate_norm"], a["data"]["notify_title"], a["data"]["notify_body"]) for a in al])[:300])
        check("ws alert_update (suppression)", any(u["data"]["read_count"] == 2 for u in got.get("alert_update", [])), json.dumps(got.get("alert_update", []))[:200])

        # alerts API
        r = await c.get("/api/alerts", params={"status": "new"}, headers=A); items = r.json()["items"]
        check("alerts list priority sorted", r.status_code == 200 and items and items[0]["priority"] == "critical", json.dumps([(i["type"], i["priority"], i["plate_norm"]) for i in items])[:300])
        aid = next(i["id"] for i in items if i["plate_norm"] == "GJ01AB1234" and i["camera"]["id"] == c1["id"])
        r = await c.get(f"/api/alerts/{aid}", headers=A); check("alert detail reads+events", r.status_code == 200 and len(r.json()["reads"]) >= 2 and len(r.json()["events"]) == 1, str(len(r.json().get("reads", []))))
        check("crop_url 200 immutable", (rr := await c.get(r.json()["read"]["crop_url"], headers=A)).status_code == 200 and "immutable" in rr.headers.get("cache-control", "") and rr.headers.get("x-sentinel-sha256"))
        check("viewer ack 403", (await c.post(f"/api/alerts/{aid}/ack", json={"note": "x"}, headers=VT)).status_code == 403)
        r = await c.post(f"/api/alerts/{aid}/ack", json={"note": "Unit dispatched"}, headers=OT); check("operator ack", r.status_code == 200 and r.json()["status"] == "acknowledged" and r.json()["acknowledged_by_username"] == "jury_operator", r.text[:200])
        check("ack twice 409", (await c.post(f"/api/alerts/{aid}/ack", json={}, headers=OT)).status_code == 409)
        r = await c.post(f"/api/alerts/{aid}/close", json={"note": "Recovered", "outcome": "resolved"}, headers=OT); check("close", r.status_code == 200 and r.json()["status"] == "closed" and r.json()["outcome"] == "resolved")
        check("close twice 409", (await c.post(f"/api/alerts/{aid}/close", json={}, headers=OT)).status_code == 409)

        # detections / sightings
        r = await c.get("/api/detections", params={"valid_only": "true"}, headers=A); check("detections list", r.status_code == 200 and r.json()["total"] >= 5 and r.json()["items"][0]["plate_display"], str(r.json()["total"]))
        rid = r.json()["items"][0]["id"]
        r = await c.get(f"/api/detections/{rid}", headers=A); check("detection detail", r.status_code == 200 and "sighting" in r.json() and "events" in r.json())
        r = await c.get("/api/detections", params={"plate": "GJ 01 AB 1235", "fuzzy": "1"}, headers=A); check("detections fuzzy", r.status_code == 200 and r.json()["total"] >= 5, str(r.json()["total"]))
        check("detections bad sort 422", (await c.get("/api/detections", params={"sort": "nope"}, headers=A)).status_code == 422)
        r = await c.get("/api/sightings", headers=A); check("sightings list", r.status_code == 200 and r.json()["total"] >= 5)

        # vehicles
        r = await c.get("/api/vehicles/search", params={"q": "GJ 01 AB 1234"}, headers=A); j = r.json()
        check("vehicle search exact+fuzzy", r.status_code == 200 and j["query"]["normalised"] == "GJ01AB1234" and len(j["exact"]) >= 4 and len(j["fuzzy"]) >= 1 and j["cameras_seen"] >= 4, f"exact={len(j['exact'])} fuzzy={len(j['fuzzy'])} seen={j['cameras_seen']}")
        fz = j["fuzzy"][0]["id"]
        r = await c.post("/api/vehicles/GJ01AB1234/confirm", json={"decisions": [{"sighting_id": fz, "decision": "confirmed"}]}, headers=OT); check("confirm", r.status_code == 200 and r.json()["saved"] == 1, r.text[:100])
        r = await c.get("/api/vehicles/GJ01AB1234/route", headers=A); j = r.json()
        check("route", r.status_code == 200 and len(j["sightings"]) >= 4 and len(j["polyline"]) == len(j["sightings"]) and j["legs"] and all("implausible_speed" in l["flags"] for l in j["legs"]) and j["total_distance_km"] > 0 and any(s["merged_sighting_ids"] for s in j["sightings"]) and j["loop_resets_in_window"] >= 1, f"stops={len(j['sightings'])} legs={len(j['legs'])} km={j['total_distance_km']} loops={j['loop_resets_in_window']}")
        r = await c.get("/api/vehicles/GJ01AB1234/route.pdf", headers=A); check("route pdf", r.status_code == 200 and r.content[:4] == b"%PDF" and r.headers.get("x-sentinel-sha256") == hashlib.sha256(r.content).hexdigest() and "route_GJ01AB1234_" in r.headers.get("content-disposition", ""), str(len(r.content)))
        check("viewer route.pdf 403", (await c.get("/api/vehicles/GJ01AB1234/route.pdf", headers=VT)).status_code == 403)

        # watchlist add live + duplicate + invalid
        r = await c.post("/api/watchlist", json={"entity_type": "vehicle", "plate": "GJ 27 XY 3456", "reason": "suspect", "priority": "high"}, headers=OT); check("watchlist add", r.status_code == 201 and r.json()["plate_norm"] == "GJ27XY3456" and r.json()["is_effective"], r.text[:150])
        wid = r.json()["id"] if r.status_code == 201 else None
        check("watchlist duplicate 409", (await c.post("/api/watchlist", json={"entity_type": "vehicle", "plate": "GJ27XY3456", "reason": "suspect"}, headers=OT)).status_code == 409)
        check("watchlist invalid plate 422", (await c.post("/api/watchlist", json={"entity_type": "vehicle", "plate": "ABC", "reason": "suspect"}, headers=OT)).status_code == 422)
        r = await c.get("/api/watchlist", params={"q": "GJ27"}, headers=A); check("watchlist list", r.status_code == 200 and r.json()["total"] >= 1)
        r = await c.get("/api/watchlist/import/template", headers=OT); check("watchlist template", r.status_code == 200 and r.text.startswith("plate,entity_type"))
        csv = "plate,entity_type,name,reason,priority,source,notes,expires_at,is_active\nGJ 09 ZZ 1111,vehicle,Import test,stolen,high,egujcop,,,true\nBAD,vehicle,x,stolen,high,egujcop,,,true\n"
        r = await c.post("/api/watchlist/import/csv", files={"file": ("w.csv", csv, "text/csv")}, headers=OT); j = r.json(); check("watchlist csv import", r.status_code == 200 and j["added"] == 1 and len(j["errors"]) == 1 and j["error_report_url"], json.dumps(j)[:200])
        if wid:
            r = await c.put(f"/api/watchlist/{wid}", json={"priority": "medium", "notes": "edited"}, headers=OT); check("watchlist update", r.status_code == 200 and r.json()["priority"] == "medium")
            check("watchlist delete", (await c.delete(f"/api/watchlist/{wid}", headers=OT)).status_code == 204)

        # events
        r = await c.post("/api/events", json={"camera_id": c1["id"], "type": "suspicious", "note": "Vehicle circling"}, headers=OT); check("event create", r.status_code == 201 and r.json()["type_label"] == "Suspicious activity")
        check("event auto type 422", (await c.post("/api/events", json={"camera_id": c1["id"], "type": "loop_reset"}, headers=OT)).status_code == 422)
        r = await c.get("/api/events", params={"type": "loop_reset,suspicious"}, headers=A); check("events list", r.status_code == 200 and r.json()["total"] >= 2)

        # dashboard
        r = await c.get("/api/dashboard/stats", headers=A); j = r.json(); check("dashboard stats", r.status_code == 200 and j["reads"]["total"] >= 5 and j["alerts"]["last_24h"] >= 1 and j["object_counts_24h"]["car"] == 7 and j["anpr_workers"], json.dumps(j)[:250])
        r = await c.get("/api/dashboard/charts", headers=A); j = r.json(); check("dashboard charts", r.status_code == 200 and j["vehicles_per_hour"] and j["top_plates"][0]["plate_norm"] == "GJ01AB1234" and j["alerts_per_camera_day"] and j["object_counts"] and j["reads_by_confidence"] and "IST" not in j["vehicles_per_hour"][0]["label_ist"], json.dumps(j)[:300])
        r = await c.get("/api/object-counts", headers=A); check("object-counts", r.status_code == 200 and r.json()["totals"].get("car") == 7, r.text[:200])

        # reports
        f, t = iso(now - timedelta(hours=1)), iso(now + timedelta(hours=1))
        r = await c.get("/api/reports/detections", params={"from": f, "to": t, "format": "csv"}, headers=A); lines = r.text.strip().splitlines()
        det_total = (await c.get("/api/detections", params={"from": f, "to": t}, headers=A)).json()["total"]
        check("detections csv", r.status_code == 200 and lines[0].startswith("captured_at_ist,captured_at_utc,camera_id") and lines[-1].startswith("# Sentinel Gujarat 1.0.0-phase1 | rows=") and len(lines) - 2 == det_total and " IST" in lines[1] and r.headers.get("x-sentinel-sha256") == hashlib.sha256(r.content).hexdigest(), f"rows={len(lines)-2} total={det_total}")
        r = await c.get("/api/reports/detections", params={"from": f, "to": t, "format": "pdf"}, headers=A); check("detections pdf", r.status_code == 200 and r.content[:4] == b"%PDF" and r.headers.get("x-sentinel-sha256"), str(len(r.content)))
        check("detections report needs from 422", (await c.get("/api/reports/detections", params={"format": "csv"}, headers=A)).status_code == 422)
        r = await c.get("/api/reports/quality", params={"from": f, "to": t}, headers=A); check("quality json", r.status_code == 200 and r.json()["reads_total"] >= 5 and r.json()["reads_per_camera"], json.dumps(r.json())[:200])
        r = await c.get("/api/qa/sample", params={"n": 5}, headers=A); s = r.json()["items"]; check("qa sample", r.status_code == 200 and len(s) >= 1)
        r = await c.post("/api/qa/labels", json={"labels": [{"read_id": s[0]["id"], "true_plate": s[0]["plate_norm"]}, {"read_id": s[1]["id"], "true_plate": ""}]}, headers=OT) if len(s) > 1 else None
        check("qa labels", r is not None and r.status_code == 200 and r.json()["saved"] == 2 and r.json()["exact"] == 1, r.text[:150] if r else "n/a")
        r = await c.get("/api/reports/quality", params={"from": f, "to": t, "format": "pdf"}, headers=A); check("quality pdf", r.status_code == 200 and r.content[:4] == b"%PDF")
        r = await c.get("/api/reports/history", headers=A); j = r.json(); check("reports history", r.status_code == 200 and j["total"] >= 4 and j["items"][0]["url"].startswith("/media/"), str(j["total"]))
        hurl = j["items"][0]["url"]
        r = await c.get(hurl, headers=A); check("report download attachment + audit", r.status_code == 200 and "attachment" in r.headers.get("content-disposition", "") and r.headers.get("x-sentinel-sha256"))
        check("operator cannot download admin report 404", (await c.get(hurl, headers=OT)).status_code == 404)

        # evidence verify + corruption
        path = hurl.replace("/media/", "", 1)
        r = await c.get("/api/evidence/verify", params={"path": path}, headers=A); check("evidence verify match", r.status_code == 200 and r.json()["match"] is True and r.json()["entity"] == "report_file", r.text[:200])
        with open(f"/data/{path}", "ab") as fh:
            fh.write(b"x")
        r = await c.get("/api/evidence/verify", params={"path": path}, headers=A); check("evidence verify corrupted -> false", r.json()["match"] is False)
        check("evidence traversal 422", (await c.get("/api/evidence/verify", params={"path": "../etc/passwd"}, headers=A)).status_code == 422)

        # clips / recordings (no recordings on this rig → empty / 409)
        r = await c.get(f"/api/recordings/{c1['id']}", headers=A); check("recordings list shape", r.status_code == 200 and r.json()["playback_path"] == f"cam_{c1['id']}" and isinstance(r.json()["segments"], list), r.text[:200])
        csvcam = (await c.get("/api/cameras", params={"source": "csv", "page_size": 1, "sort": "external_id", "order": "asc"}, headers=A)).json()["items"][0]
        r = await c.get(f"/api/recordings/{csvcam['id']}/play", params={"at": iso(now)}, headers=A); check("recordings play unavailable", r.status_code == 200 and r.json()["available"] is False and r.json()["url"] is None, r.text[:200])
        r = await c.post("/api/clips", json={"camera_id": csvcam["id"], "start_at": iso(now), "duration_s": 30}, headers=OT); check("clip without recording 409", r.status_code == 409, r.text[:150])
        check("clips list", (await c.get("/api/clips", headers=A)).status_code == 200)

        # zones
        r = await c.post("/api/zones", json={"camera_id": c1["id"], "name": "Gate apron", "polygon": [[0.1, 0.5], [0.9, 0.5], [0.9, 0.95], [0.1, 0.95]], "active_from": "22:00", "active_to": "06:00", "classes": ["person"], "dwell_s": 2, "priority": "medium"}, headers=A); check("zone create", r.status_code == 201 and r.json()["active_from"] == "22:00", r.text[:200])
        zid = r.json().get("id")
        check("operator zone 403", (await c.post("/api/zones", json={"camera_id": c1["id"], "name": "x", "polygon": [[0, 0], [1, 0], [1, 1]]}, headers=OT)).status_code == 403)
        r = await c.get("/api/internal/anpr-config", params={"mode": "live"}, headers={"X-API-Key": IK}); check("zone in anpr-config", any(z["name"] == "Gate apron" for cam in r.json()["cameras"] for z in cam["zones"]))
        # intrusion event → alert
        r = await c.post("/api/internal/events", headers={"X-API-Key": IK}, files={"payload": (None, json.dumps({"camera_id": c1["id"], "events": [{"type": "intrusion", "occurred_at": iso(datetime.now(timezone.utc)), "note": "person in zone 'Gate apron' for 2.4 s", "zone_id": zid, "class": "person", "dwell_s": 2.4, "frame_file": "event_frame_0"}]})), "event_frame_0": ("f.jpg", jpeg(960, 540, "intrusion"), "image/jpeg")})
        check("intrusion event", r.status_code == 200 and r.json()["events_created"] == 1, r.text[:150])
        r = await c.get("/api/alerts", params={"type": "intrusion"}, headers=A); check("intrusion alert with snapshot", r.json()["total"] >= 1 and r.json()["items"][0]["snapshot_url"], r.text[:200])
        if zid:
            check("zone delete", (await c.delete(f"/api/zones/{zid}", headers=A)).status_code == 204)

        # object counts endpoint
        r = await c.post("/api/internal/object-counts", headers={"X-API-Key": IK}, json={"camera_id": c1["id"], "object_counts": [{"minute": iso(now.replace(second=0, microsecond=0)), "class": "bus", "count": 2}]}); check("internal object-counts", r.status_code == 200 and r.json()["object_counts_upserted"] == 1)

        # external
        r = await c.get("/api/external/vahan/GJ01AB1234", headers=OT); check("vahan mock", r.status_code == 200 and r.json()["adapter"] == "vahan_mock" and "found" in r.json(), r.text[:120])
        r = await c.get("/api/external/sarthi/GJ0120200012345", headers=OT); check("sarthi mock", r.status_code == 200 and r.json()["adapter"] == "sarthi_mock")
        check("viewer external 403", (await c.get("/api/external/vahan/GJ01AB1234", headers=VT)).status_code == 403)

        # settings
        r = await c.put("/api/settings", json={"values": {"catalogue.base_url": "http://sg-api:8000/nowhere", "catalogue.auth_password": "********"}}, headers=A); check("settings put", r.status_code == 200)
        r = await c.post("/api/settings/catalogue/test", headers=A); check("catalogue test fails cleanly", r.status_code == 200 and r.json()["ok"] is False, r.text[:150])
        r = await c.post("/api/cameras/import/sandbox", json={}, headers=A); check("import 502 upstream", r.status_code == 502 and r.json()["code"] == "upstream_error" and "nowhere" in r.json()["detail"], r.text[:150])
        r = await c.put("/api/settings", json={"values": {"catalogue.base_url": "http://sg-api:8000/mock-sandbox"}}, headers=A)
        r = await c.post("/api/settings/catalogue/test", headers=A); check("catalogue test ok", r.json()["ok"] is True and r.json()["count"] == 50 and r.json()["mapped_sample"]["external_id"] == "1", r.text[:150])
        check("settings unknown key 422", (await c.put("/api/settings", json={"values": {"nope.key": 1}}, headers=A)).status_code == 422)
        check("settings bad value 422", (await c.put("/api/settings", json={"values": {"gap.grid_m": 5}}, headers=A)).status_code == 422)
        check("viewer settings 403", (await c.get("/api/settings", headers=VT)).status_code == 403)
        r = await c.get("/api/settings/public", headers=VT); check("settings public", r.status_code == 200 and r.json()["mock_sandbox"] is True and "route.speed_flag_kmh" in r.json())

        # webhooks + sink
        r = await c.post("/api/webhooks", json={"name": "sink", "url": "http://localhost:8000/mock-sandbox/webhook-sink", "secret": "s3cret", "event_types": ["alert.created", "alert.updated"]}, headers=A); check("webhook create", r.status_code == 201 and r.json()["secret"] == "********", r.text[:150])
        whid = r.json()["id"]
        r = await c.post(f"/api/webhooks/{whid}/test", headers=A); check("webhook test 204", r.status_code == 200 and r.json()["status"] == 204, r.text)
        # trigger alert on camera 6 for a different plate → webhook delivery
        t2 = datetime.now(timezone.utc); k2 = f"{c6['id']}:GJ18CD5678:{int(t2.timestamp()*1000)}"
        await c.post("/api/internal/detections", headers={"X-API-Key": IK}, files={"payload": (None, json.dumps({"worker_id": "e2e", "mode": "live", "camera_id": c6["id"], "sent_at": iso(t2), "reads": [{"captured_at": iso(t2), "plate_raw": "GJ18CD5678", "confidence": 0.9, "crop_file": "crop_0", "sighting_key": k2}], "sightings": [{"key": k2, "plate_norm": "GJ18CD5678", "first_seen": iso(t2), "last_seen": iso(t2), "read_count": 1, "best_conf": 0.9, "best_crop_file": "crop_0", "closed": False}]})), "crop_0": ("c.jpg", jpeg(text="GJ18CD5678"), "image/jpeg")})
        await asyncio.sleep(2)
        r = await c.get("/api/mock-sandbox/webhook-sink", headers=A); d = r.json()["deliveries"]
        hit = [x for x in d if x["headers"].get("x-sentinel-event") == "alert.created"]
        check("webhook delivered with signature", hit and hit[0]["headers"].get("x-sentinel-signature", "").startswith("sha256="), json.dumps(d)[:200])
        r = await c.get("/api/webhooks", headers=A); check("webhook last_status 204", r.json()["items"][0]["last_status"] == 204, r.text[:200])
        check("webhook delete", (await c.delete(f"/api/webhooks/{whid}", headers=A)).status_code == 204)

        # users & api keys
        r = await c.post("/api/users", json={"username": "e2e_user", "password": "Sentinel@E2E2026", "full_name": "E2E", "role": "operator"}, headers=A); check("user create", r.status_code in (201, 409), r.text[:120])
        uid = r.json().get("id")
        check("weak password 422", (await c.post("/api/users", json={"username": "e2e_weak", "password": "short", "full_name": "x", "role": "viewer"}, headers=A)).status_code == 422)
        r = await c.post("/api/api-keys", json={"name": "e2e-key", "scope": "bulk"}, headers=A); check("api key create shows key once", r.status_code in (201, 409) and (r.status_code == 409 or r.json()["key"].startswith("sk_")), r.text[:100])
        if r.status_code == 201:
            newkey = r.json()["key"]
            check("new bulk key works", (await c.post("/api/v1/cameras/bulk", json=[], headers={"X-API-Key": newkey})).status_code == 200)
            check("api key delete", (await c.delete(f"/api/api-keys/{r.json()['id']}", headers=A)).status_code == 204)
            check("deactivated key 401", (await c.post("/api/v1/cameras/bulk", json=[], headers={"X-API-Key": newkey})).status_code == 401)
        r = await c.get("/api/me/wall-layout", headers=A); check("wall layout default", r.status_code == 200 and r.json()["grid"] == 4 and len(r.json()["tiles"]) == 4, r.text[:150])
        r = await c.put("/api/me/wall-layout", json={"grid": 9, "tiles": [{"slot": 0, "camera_id": c1["id"]}]}, headers=A); check("wall layout save", r.status_code == 200 and (await c.get("/api/me/wall-layout", headers=A)).json()["grid"] == 9)

        # camera CRUD + maintenance + retire + own camera
        r = await c.post("/api/cameras", json={"external_id": "OWN-GATE-01", "name": "Dynatech Office Gate (private society camera)", "department_code": "POLICE", "source": "own", "ownership": "private", "type": "ip", "lat": 23.033, "lon": 72.515, "district": "Ahmedabad", "police_station": "Satellite", "rtsp_url": "rtsp://mediamtx:8554/own_gate", "codec": "H264", "connectivity_type": "wifi", "anpr_enabled": True, "record_enabled": True}, headers=A)
        check("own camera create", r.status_code in (201, 409), r.text[:150])
        own = r.json()
        if r.status_code == 201:
            check("own camera relay path", own["relay_path"] == f"cam_{own['id']}" and own["source"] == "own" and own["ownership"] == "private")
            r = await c.put(f"/api/cameras/{own['id']}", json={"name": "Dynatech Gate", "codec": "H265"}, headers=A); check("camera update recreates relay", r.status_code == 200 and r.json()["play_path"].endswith("_h264"), r.text[:150])
            r = await c.put(f"/api/cameras/{own['id']}/maintenance", json={"maintenance_status": "faulty", "maintenance_note": "lens"}, headers=A); check("maintenance", r.status_code == 200 and r.json()["maintenance_status"] == "faulty")
            r = await c.get(f"/api/cameras/{own['id']}/health", headers=A); check("camera health", r.status_code == 200 and "log" in r.json())
            check("retire", (await c.delete(f"/api/cameras/{own['id']}", headers=A)).status_code == 204)
            check("retired hidden", (await c.get("/api/cameras", params={"q": "Dynatech"}, headers=A)).json()["total"] == 0 and (await c.get("/api/cameras", params={"q": "Dynatech", "include_retired": "true"}, headers=A)).json()["total"] == 1)
        check("camera 422 shape", (r := await c.post("/api/cameras", json={"external_id": "BAD", "name": "x", "lat": 95, "lon": 1}, headers=A)).status_code == 422 and r.json()["code"] == "validation_error" and r.json()["errors"][0]["field"] == "lat", r.text[:200])
        check("operator camera create 403", (await c.post("/api/cameras", json={"external_id": "Q", "name": "x"}, headers=OT)).status_code == 403)
        r = await c.get("/api/cameras/import/template", headers=A); check("csv template", r.status_code == 200 and r.text.startswith("external_id,name,department_code"))

        # gap analysis
        t0 = time.time(); r = await c.get("/api/gap-analysis", headers=A); j = r.json()
        check("gap analysis", r.status_code == 200 and j["summary"]["zero_coverage_cells"] > 0 and len(j["zero_coverage"]["features"]) > 0 and any(u["district"] == "Gandhinagar" for u in j["uncovered_pois"]) and any(a["name"] == "Okha Checkpost" for a in j["ageing"]) and j["department_gaps"] and j["recommendations"], f"{time.time()-t0:.1f}s summary={json.dumps(j['summary'])}")
        r = await c.get("/api/gap-analysis/export", params={"format": "csv"}, headers=A); check("gap csv", r.status_code == 200 and r.text.startswith("section,") and r.headers.get("x-sentinel-sha256"))
        r = await c.get("/api/gap-analysis/export", params={"format": "pdf"}, headers=A); check("gap pdf", r.status_code == 200 and r.content[:4] == b"%PDF", str(len(r.content)))
        r = await c.get("/api/cameras/export", params={"format": "csv"}, headers=OT); check("cameras export operator", r.status_code == 200 and "# Exported by jury_operator" in r.text and r.headers.get("x-sentinel-sha256"))
        check("viewer export 403", (await c.get("/api/cameras/export", headers=VT)).status_code == 403)

        # health summary & healthz
        r = await c.get("/api/health/summary", headers=A); j = r.json(); check("health summary", r.status_code == 200 and j["anpr_workers"] and j["anpr_workers"][0]["id"] == "live-e2e", json.dumps(j["cameras"]) + " workers=" + str(len(j["anpr_workers"])))
        r = await c.get("/healthz"); check("healthz", r.status_code == 200 and r.json()["db"] == "ok" and r.json()["anpr_workers"] == 1, r.text)

        # audit
        r = await c.get("/api/audit", params={"action": "alert.ack"}, headers=A); check("audit alert.ack by operator", r.json()["total"] >= 1 and r.json()["items"][0]["actor"] == "jury_operator" and r.json()["items"][0]["ip"], json.dumps(r.json()["items"][:1])[:200])
        r = await c.get("/api/audit", params={"page_size": 500}, headers=A); acts = {i["action"] for i in r.json()["items"]}
        codes = [(await c.post("/api/auth/login", json={"username": "jury_admin", "password": "wrong"})).status_code for _ in range(11)]
        check("login rate limit 429", codes[-1] == 429 and codes[0] == 401, str(codes))
        r = await c.get("/api/audit", params={"page_size": 500}, headers=A); acts = {i["action"] for i in r.json()["items"]}
        need = {"auth.login", "auth.login_failed", "camera.import_sandbox", "camera.import_csv", "camera.import_bulk", "camera.export", "stream.view", "vehicle.search", "vehicle.route", "vehicle.confirm", "report.route", "report.detections", "report.gap", "report.quality", "report.download", "evidence.verify", "external.lookup", "settings.update", "webhook.create", "watchlist.create", "watchlist.import", "alert.ack", "alert.close", "event.create", "zone.create", "qa.label", "camera.create", "camera.update", "camera.retire", "camera.maintenance", "user.create", "apikey.create"}
        check("audit action coverage", need <= acts, "missing=" + str(sorted(need - acts)))
        r = await c.get("/api/audit/export", params={"format": "csv"}, headers=A); check("audit csv export", r.status_code == 200 and r.text.startswith("id,ts_ist"))
        check("operator audit 403", (await c.get("/api/audit", headers=OT)).status_code == 403)
        rows = [i for i in (await c.get("/api/audit", params={"action": "camera.import_bulk", "page_size": 50}, headers=A)).json()["items"] if not (i["after"] or {}).get("http_status")]
        check("apikey actor in audit", rows and rows[0]["actor"].startswith("apikey:") and rows[0]["role"] == "apikey", json.dumps(rows[:1])[:200])

        # change password
        r = await c.post("/api/auth/change-password", json={"current_password": "Sentinel@View2026", "new_password": "Sentinel@View2027"}, headers=VT); check("change password", r.status_code == 204, r.text[:100])
        await c.post("/api/auth/change-password", json={"current_password": "Sentinel@View2027", "new_password": "Sentinel@View2026"}, headers=VT)
        r = await c.post("/api/auth/logout", headers=A); check("logout clears cookie", r.status_code == 204 and "sg_session" in r.headers.get("set-cookie", ""), r.headers.get("set-cookie", "")[:80])
        r = await c.get("/api/auth/verify", headers=A); check("auth verify 204 headers", r.status_code == 204 and r.headers.get("x-sentinel-user") == "jury_admin")
        check("auth verify 401", (await c.get("/api/auth/verify")).status_code == 401)

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed: {failed}")
    sys.exit(1 if failed else 0)


asyncio.run(main())
