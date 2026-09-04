"""The 50 fixed mock cameras (CONTRACT §6.2), loaded from seeds/mock_catalogue.json."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from app.core.config import settings


@lru_cache
def load() -> list[dict[str, Any]]:
    path = settings.seeds_dir / "mock_catalogue.json"
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return data


def summary() -> dict[str, Any]:
    items = load()
    return {"name": "Sentinel Gujarat mock sandbox", "cameras": len(items), "live": sum(1 for c in items if c.get("live"))}
