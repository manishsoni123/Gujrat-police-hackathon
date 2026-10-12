"""Gap analysis survives a zero-coverage grid timeout (needs a database: marked `db`, skipped without DATABASE_URL).

Regression of 13 Oct 2026: the timeout handler rolled the whole session back, which expired the Camera rows
`_compute` had already loaded; the next attribute read lazy-loaded outside the async context
(sqlalchemy MissingGreenlet -> HTTP 500 on GET /api/gap-analysis under load).
"""

from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.db


def test_zero_coverage_timeout_does_not_break_the_report(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.rbac import Scope
    from app.db.session import SessionLocal
    from app.services import gap_analysis as g

    monkeypatch.setattr(g, "ZERO_COVERAGE_TIMEOUT_MS", 1)  # force the grid query to time out

    async def run() -> dict:
        async with SessionLocal() as db:
            data = await g.compute(db, Scope(), {"coverage_radius_m": 150, "poi_radius_m": 300, "grid_m": 500, "ageing_years": 5},
                                   None, True)
            # the session is still usable after the savepoint rollback
            from sqlalchemy import text

            assert (await db.execute(text("SELECT 1"))).scalar() == 1
            return data

    data = asyncio.run(run())
    assert "summary" in data and data["summary"]["cameras_total"] >= 0
