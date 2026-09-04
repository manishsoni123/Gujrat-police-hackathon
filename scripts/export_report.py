#!/usr/bin/env python3
"""Call the reports endpoints and save the files, verifying the X-Sentinel-Sha256 header.

    python scripts/export_report.py --api http://localhost --user jury_admin --password ... \
        --from 2026-09-04T08:00:00Z --to 2026-09-04T10:00:00Z --out ./exports
    python scripts/export_report.py ... --route GJ01AB1234          # also the route PDF
    python scripts/export_report.py ... --gap                       # also gap-analysis CSV + PDF

Saves detections_<from>_<to>_IST.csv / .pdf (the "output report", CONTRACT.md section 5.15),
optionally the route PDF (section 5.10) and the gap-analysis exports (section 5.8), and prints
the SHA-256 of each file next to the server's hash. Needs only ``requests`` (any Python 3.9+;
runs inside the anpr or api image: ``docker compose run --rm anpr-live python scripts/export_report.py ...``).
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def login(api: str, user: str, password: str, timeout: float) -> str:
    resp = requests.post(f"{api}/api/auth/login", json={"username": user, "password": password}, timeout=timeout)
    if resp.status_code != 200:
        raise SystemExit(f"login failed: {resp.status_code} {resp.text[:200]}")
    return resp.json()["access_token"]


def download(api: str, token: str, path: str, params: dict, out_dir: Path, fallback_name: str, timeout: float) -> Path | None:
    resp = requests.get(f"{api}/api{path}", params=params, headers={"Authorization": f"Bearer {token}"}, timeout=timeout)
    if resp.status_code != 200:
        print(f"  {path}: {resp.status_code} {resp.text[:200]}", file=sys.stderr)
        return None
    name = fallback_name
    cd = resp.headers.get("Content-Disposition", "")
    m = re.search(r'filename="?([^";]+)"?', cd)
    if m:
        name = m.group(1)
    dest = out_dir / name
    dest.write_bytes(resp.content)
    local = hashlib.sha256(resp.content).hexdigest()
    server = resp.headers.get("X-Sentinel-Sha256", "")
    status = "match" if server and server.lower() == local else ("no header" if not server else "MISMATCH")
    print(f"  {dest} ({len(resp.content)} bytes) sha256={local[:16]}... server={server[:16] or '-'}... {status}")
    return dest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api", default=os.environ.get("API", "http://localhost"))
    parser.add_argument("--user", default=os.environ.get("USER_NAME", "jury_admin"))
    parser.add_argument("--password", default=os.environ.get("JURY_ADMIN_PASSWORD", "Sentinel@Admin2026"))
    parser.add_argument("--from", dest="from_", help="ISO-8601 UTC (default: now - 2 h)")
    parser.add_argument("--to", help="ISO-8601 UTC (default: now)")
    parser.add_argument("--camera-id", type=int)
    parser.add_argument("--department-id", type=int)
    parser.add_argument("--min-conf", type=float)
    parser.add_argument("--valid-only", action="store_true")
    parser.add_argument("--route", help="plate whose route PDF should be exported as well")
    parser.add_argument("--gap", action="store_true", help="also export the gap-analysis CSV and PDF")
    parser.add_argument("--out", default="./exports")
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args(argv)

    now = datetime.now(timezone.utc)
    frm = args.from_ or iso(now - timedelta(hours=2))
    to = args.to or iso(now)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    api = args.api.rstrip("/")
    token = login(api, args.user, args.password, args.timeout)

    params: dict = {"from": frm, "to": to}
    if args.camera_id:
        params["camera_id"] = args.camera_id
    if args.department_id:
        params["department_id"] = args.department_id
    if args.min_conf is not None:
        params["min_conf"] = args.min_conf
    if args.valid_only:
        params["valid_only"] = "true"

    stamp = frm.replace(":", "").replace("-", "")[:15] + "_" + to.replace(":", "").replace("-", "")[:15]
    print(f"detections report {frm} -> {to}")
    files = [
        download(api, token, "/reports/detections", {**params, "format": "csv"}, out_dir, f"detections_{stamp}.csv", args.timeout),
        download(api, token, "/reports/detections", {**params, "format": "pdf"}, out_dir, f"detections_{stamp}.pdf", args.timeout),
    ]
    if args.route:
        plate = re.sub(r"[^A-Z0-9]", "", args.route.upper())
        print(f"route report {plate}")
        files.append(download(api, token, f"/vehicles/{plate}/route.pdf", {"from": frm, "to": to}, out_dir, f"route_{plate}.pdf", args.timeout))
    if args.gap:
        print("gap analysis")
        files.append(download(api, token, "/gap-analysis/export", {"format": "csv"}, out_dir, "gap_analysis.csv", args.timeout))
        files.append(download(api, token, "/gap-analysis/export", {"format": "pdf"}, out_dir, "gap_analysis.pdf", args.timeout))
    saved = [f for f in files if f]
    print(f"{len(saved)} file(s) saved under {out_dir}")
    return 0 if saved else 1


if __name__ == "__main__":
    sys.exit(main())
