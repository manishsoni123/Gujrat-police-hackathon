"""Organiser-sandbox import (`catalogue.source = sentinel_portal`): catalogue → probe → upsert → relay → summary.

Shared by `POST /cameras/import/sandbox` (JSON body) and `POST /cameras/import/sandbox/file` (multipart upload).
Probes go directly to the sandbox (one short RTSP session per camera; the relay paths are created afterwards),
`sandbox.probe_timeout_s` cap each, and follow the organiser etiquette (`probe_paced`): at most
`sandbox.probe_parallel` (≤ 6) in flight, every dial through the process-wide `dial_gate` (one new upstream
connection per 3 s, shared with the relay and the health poller), and no further dials once the sandbox has
refused our credentials three times in a row. 30 cameras therefore take ~90-100 s of probing (29 × 3 s of
spacing plus the last answer). Relay-source policy (CONTRACT Amendments 2026-09-05): after the upsert
`ensure_relay_policies` re-asserts, for every non-retired camera, a **persistent** relay source for the cameras
we actually process (`anpr_enabled`, own gate) and on-demand with a 10-minute close-after for the rest — paced,
and only where the running relay differs, so a re-import never re-dials the live cameras.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import ProbeResult, SourceCamera
from app.adapters.rtsp import classify_probe_error
from app.adapters.sentinel_portal import SentinelPortalAdapter
from app.core.tz import iso_z, utcnow
from app.db.models import Camera
from app.services import settings_service as cfg
from app.services.camera_importer import ImportOutcome, upsert_cameras
from app.services.mediamtx_client import PERSISTENT, dial_gate, play_path, relay_policy
from app.services.mediamtx_client import client as mtx
from app.services.sentinel_catalogue import source_camera_row

log = logging.getLogger("sentinel.import")

# Consecutive credential refusals (RTSP 401/403) after which the import stops dialling: every camera of the
# sandbox uses the same e-mail + access password, so the remaining ones would only add failed logins against a
# server that refuses bursts.
AUTH_REFUSALS_BEFORE_STOP = 3
_AUTH_REFUSAL_RE = re.compile(r"(Server returned 40[13]\b|\b401 Unauthorized|\b403 Forbidden)", re.I)


class _Gate(Protocol):
    async def wait(self) -> None: ...


def is_auth_refusal(error: str | None) -> bool:
    """True for an RTSP 401/403 answer: the server refused our credentials (or this client), which says nothing
    about the camera itself — unlike 400/404 or "path not configured". Pure."""
    return bool(error) and _AUTH_REFUSAL_RE.search(error) is not None


@dataclass
class PacedProbes:
    """`probe_paced` outcome: results by `external_id` for every camera actually dialled, the ids that were not
    dialled because the sandbox kept refusing our credentials, and the dial start offsets (s) for the log."""

    results: dict[str, ProbeResult] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    dial_offsets_s: list[float] = field(default_factory=list)


async def probe_paced(
    cams: Sequence[SourceCamera],
    probe: Callable[[SourceCamera], Awaitable[ProbeResult]],
    *,
    parallel: int,
    gate: _Gate = dial_gate,
    stop_after_auth_refusals: int = AUTH_REFUSALS_BEFORE_STOP,
) -> PacedProbes:
    """Probe `cams` in catalogue order with at most `parallel` probes in flight and every dial passing `gate`
    (the in-flight slot is taken before the gate, so at most `parallel` cameras are dialling or next in line).
    After `stop_after_auth_refusals` consecutive 401/403 answers the cameras not yet dialled are skipped."""
    sem = asyncio.Semaphore(max(1, int(parallel)))
    out = PacedProbes()
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    refusals = 0

    def stopped() -> bool:
        return stop_after_auth_refusals > 0 and refusals >= stop_after_auth_refusals

    async def one(cam: SourceCamera) -> None:
        nonlocal refusals
        async with sem:
            if stopped():
                out.skipped.append(cam.external_id)
                return
            await gate.wait()
            if stopped():  # the refusal arrived while this camera waited for its dial slot
                out.skipped.append(cam.external_id)
                return
            out.dial_offsets_s.append(round(loop.time() - t0, 2))
            res = await probe(cam)
            out.results[cam.external_id] = res
            refusals = refusals + 1 if (not res.ok and is_auth_refusal(res.error)) else 0
            log.info(
                "import probe %s: %s%s (%s ms)", cam.external_id, (f"ok {res.codec}" if res.ok else "failed"),
                f" {res.resolution}" if res.ok and res.resolution else f": {res.error}", res.duration_ms,
            )

    await asyncio.gather(*(one(c) for c in cams))
    order = {c.external_id: i for i, c in enumerate(cams)}
    out.skipped.sort(key=lambda x: order.get(x, 0))
    return out


def unprobed_row(cam: SourceCamera, error: str | None) -> dict[str, Any]:
    """Probe-report row for a camera that was not dialled (probing off, no credentials, or skipped)."""
    return {
        "external_id": cam.external_id, "name": cam.name, "ok": None, "codec": None, "resolution": None, "fps": None, "live": None,
        "duration_ms": None, "error": error, "location_confidence": cam.extra.get("location_confidence"),
        "department_code": cam.department_code, "transcode": None,
    }


def skipped_message(refusals: int) -> str:
    return (
        f"not probed: the sandbox refused the stream credentials (RTSP 401/403) {refusals} times in a row; check "
        "Settings → Organiser stream relay (registered e-mail / access password) and re-run the import later"
    )


async def ensure_relay_policies(db: AsyncSession) -> dict[str, int]:
    """Re-assert the relay-source policy on every non-retired camera with a source URL (not only the rows this
    import touched, so a camera whose `anpr_enabled` flag was flipped by hand is covered too). Returns counts:
    `persistent` (cameras that must hold one upstream connection), `applied` (paths actually re-configured —
    each one restarts that relay source once, spaced 3 s apart) and `failed`."""
    cams = (await db.execute(select(Camera).where(Camera.status != "retired", Camera.rtsp_url.isnot(None)))).scalars().all()
    out = {"persistent": 0, "applied": 0, "failed": 0}
    for cam in cams:
        if relay_policy(cam) == PERSISTENT:
            out["persistent"] += 1
        res = await mtx.ensure_relay_policy(cam)
        if not res.ok:
            out["failed"] += 1
            log.warning("relay policy for camera %s not applied: %s", cam.id, res.detail)
        elif res.detail in ("patched", "created"):
            out["applied"] += 1
    return out


def apply_probe(cam: SourceCamera, res: ProbeResult, at: str) -> dict[str, Any]:
    """Fold a probe result into the SourceCamera (codec/resolution/fps/live + metadata.probe); returns the
    per-camera report row. `live` is True on success, False when the server definitively refused the path
    (4xx/5xx, unknown path) and None (unknown → still health-probed later) on a timeout or network error.
    A 401/403 (`kind` "auth") refuses our credentials or this client, not the camera, so it is unknown too:
    `live=false` would stop the health poller from ever probing the camera again."""
    if res.ok:
        cam.codec = res.codec or "UNKNOWN"
        cam.resolution = res.resolution
        cam.fps = int(res.fps) if res.fps else None
        cam.live = True
        kind = "ok"
    else:
        kind = classify_probe_error(res.error)
        if kind == "definitive" and is_auth_refusal(res.error):
            kind = "auth"
        cam.live = False if kind == "definitive" else None
    meta = cam.extra.setdefault("metadata", {})
    meta["probe"] = {"at": at, "ok": res.ok, "codec": res.codec, "resolution": res.resolution, "fps": res.fps, "profile": res.profile, "b_frames": res.b_frames, "duration_ms": res.duration_ms, "error": res.error, "kind": kind}
    return {
        "external_id": cam.external_id, "name": cam.name, "ok": res.ok, "codec": res.codec, "resolution": res.resolution,
        "fps": res.fps, "live": cam.live, "duration_ms": res.duration_ms, "error": res.error,
        "location_confidence": cam.extra.get("location_confidence"), "department_code": cam.department_code,
        "transcode": (res.codec == "H265" or bool(res.b_frames)), "b_frames": res.b_frames,
    }


@dataclass
class CatalogueProbe:
    """Probe phase of an import: report rows (catalogue order), cameras dialled, wall time, summary warnings."""

    rows: list[dict[str, Any]] = field(default_factory=list)
    probed: int = 0
    ms: int = 0
    warnings: list[dict[str, Any]] = field(default_factory=list)


async def probe_catalogue(
    cams: Sequence[SourceCamera],
    probe: Callable[[SourceCamera], Awaitable[ProbeResult]],
    *,
    parallel: int,
    gate: _Gate = dial_gate,
) -> CatalogueProbe:
    """Paced probes (`probe_paced`) folded into the cameras (`apply_probe`) and the report rows."""
    t0 = time.perf_counter()
    paced = await probe_paced(cams, probe, parallel=parallel, gate=gate)
    out = CatalogueProbe(ms=int((time.perf_counter() - t0) * 1000))
    at = iso_z(utcnow())
    skipped = set(paced.skipped)
    message = skipped_message(AUTH_REFUSALS_BEFORE_STOP)
    for c in cams:
        res = paced.results.get(c.external_id)
        if res is not None:
            out.rows.append(apply_probe(c, res, at))
            out.probed += 1
        elif c.external_id in skipped:
            out.rows.append(unprobed_row(c, message))
    ok = sum(1 for r in paced.results.values() if r.ok)
    spacing = getattr(gate, "spacing_s", None)
    log.info(
        "import probes: %d dialled, %d ok, %d skipped in %.1f s (parallel %d, dial spacing %s s, dials at %s s)",
        out.probed, ok, len(skipped), out.ms / 1000, max(1, int(parallel)), spacing, paced.dial_offsets_s,
    )
    if skipped:
        out.warnings.append({"row": 0, "external_id": None, "field": "probe", "message": f"{len(skipped)} cameras {message}"})
        log.warning("import probes stopped: %d consecutive credential refusals, %d cameras not dialled", AUTH_REFUSALS_BEFORE_STOP, len(skipped))
    return out


async def measure_first_stream(db: AsyncSession, outcome: ImportOutcome) -> int | None:
    """Time from requesting the HLS manifest of the first live camera (starts the on-demand pull) until the relay
    path reports ready (CONTRACT §5.2 / Amendments: the "onboarding time" figure)."""
    first_live = next((c for c in outcome.touched if c.live and c.rtsp_url), None)
    if first_live is None:
        first_live = (
            await db.execute(select(Camera).where(Camera.source == "sandbox", Camera.live.is_(True), Camera.rtsp_url.isnot(None), Camera.status != "retired").order_by(Camera.id).limit(1))
        ).scalar_one_or_none()
    if first_live is None:
        return None
    path = play_path(first_live.id, first_live.codec, getattr(first_live, "meta", None))
    await dial_gate.wait()  # the manifest request makes an idle on-demand path dial upstream; not part of the figure
    t0 = time.perf_counter()
    await mtx.trigger_hls(path)
    elapsed = await mtx.wait_ready(path, timeout_s=20.0)
    return int((time.perf_counter() - t0) * 1000) if elapsed is not None else None


async def run_sentinel_import(
    db: AsyncSession,
    *,
    cameras_json: bytes | None = None,
    enrichment_csv: str | None = None,
    dry_run: bool = False,
    measure_first: bool = True,
    probe: bool = True,
) -> dict[str, Any]:
    """Raises `CatalogueError` when no catalogue can be read (the router turns it into 502 upstream_error)."""
    started = utcnow()
    adapter = SentinelPortalAdapter(cameras_json=cameras_json, enrichment_csv=enrichment_csv)
    cams = await adapter.list_cameras()
    if probe and adapter.stream.configured and cams:
        probed_phase = await probe_catalogue(cams, adapter.probe, parallel=adapter.probe_parallel)
    else:
        error = None if adapter.stream.configured else "stream credentials not configured"
        probed_phase = CatalogueProbe(rows=[unprobed_row(c, error) for c in cams])
    probe_rows, probed, probe_ms = probed_phase.rows, probed_phase.probed, probed_phase.ms
    rows =[(i + 1, i, source_camera_row(c)) for i, c in enumerate(cams)]
    outcome = await upsert_cameras(
        db, rows, source="sandbox", created_by=None, created_via="sandbox", scope=None, dry_run=dry_run,
        department_aliases=cfg.get("catalogue.department_aliases") or {}, catalogue_defaults=True, match_any_source=True,
    )
    relay_policy_counts = {"persistent": 0, "applied": 0, "failed": 0}
    if not dry_run:
        try:
            relay_policy_counts = await ensure_relay_policies(db)
        except Exception:  # noqa: BLE001
            log.exception("relay policy pass failed (the health poller re-asserts it every tick)")
    first_stream_ms = None
    if measure_first and not dry_run:
        try:
            first_stream_ms = await measure_first_stream(db, outcome)
        except Exception:  # noqa: BLE001
            log.exception("first-stream measurement failed")
    finished = utcnow()
    ok_count = sum(1 for p in probe_rows if p["ok"])
    h265 = sum(1 for p in probe_rows if p["codec"] == "H265")
    warnings = [w.as_dict() for w in outcome.warnings]
    warnings.extend(adapter.warnings)
    warnings.extend({"row": 0, "external_id": None, "field": "catalogue", "message": w} for w in adapter.info.warnings)
    warnings.extend(probed_phase.warnings)
    return {
        "source": "sentinel_portal",
        "catalogue_mode": adapter.info.mode,
        "source_url": adapter.info.source,
        "enrichment_source": adapter.enrichment_origin,
        "enrichment_rows": len(adapter.enrichment.rows) if adapter.enrichment else 0,
        "enrichment_confidence": adapter.enrichment.confidence_counts if adapter.enrichment else None,
        "missing_enrichment": adapter.missing_enrichment,
        "started_at": iso_z(started),
        "finished_at": iso_z(finished),
        "duration_ms": int((finished - started).total_seconds() * 1000),
        "fetched": len(cams),
        "added": outcome.added,
        "updated": outcome.updated,
        "unchanged": outcome.unchanged,
        "errors": [e.as_dict() for e in outcome.errors],
        "warnings": warnings,
        "relay_paths_created": outcome.relay_paths_created,
        "relay_paths_failed": outcome.relay_paths_failed,
        "relay_persistent": relay_policy_counts["persistent"],
        "relay_policy_applied": relay_policy_counts["applied"],
        "anpr_enabled": outcome.anpr_enabled,
        "first_stream_ready_ms": first_stream_ms,
        "probed": probed,
        "probe_ok": ok_count,
        "probe_h265": h265,
        "probe_ms": probe_ms,
        "probes": probe_rows,
        "unmapped_fields": [],
        "dry_run": dry_run,
    }


def audit_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Counts only (no per-camera rows, no error strings) for the audit `after` column."""
    keep = ("source", "catalogue_mode", "source_url", "enrichment_source", "enrichment_rows", "started_at", "finished_at", "duration_ms", "fetched", "added", "updated", "unchanged", "relay_paths_created", "relay_paths_failed", "relay_persistent", "relay_policy_applied", "anpr_enabled", "first_stream_ready_ms", "probed", "probe_ok", "probe_h265", "probe_ms", "dry_run")
    out = {k: summary.get(k) for k in keep}
    out["errors"] = len(summary.get("errors") or [])
    out["warnings"] = len(summary.get("warnings") or [])
    out["missing_enrichment"] = len(summary.get("missing_enrichment") or [])
    return out
