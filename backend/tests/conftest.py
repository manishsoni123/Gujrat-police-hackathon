"""Pytest configuration.

Unit tests never touch PostgreSQL. Tests marked `db` need a reachable database and are
skipped unless `DATABASE_URL` is set in the environment (CONTRACT §13.3 check 29).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("SCHEDULER_ENABLED", "0")
os.environ.setdefault("SEED_ON_START", "0")

SEEDS_DIR = Path(__file__).resolve().parent.parent / "seeds"


@pytest.fixture(scope="session")
def seeds_dir() -> Path:
    return SEEDS_DIR


@pytest.fixture(scope="session")
def sample_cameras_csv(seeds_dir: Path) -> str:
    return (seeds_dir / "cameras_sample.csv").read_text(encoding="utf-8")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("DATABASE_URL"):
        return
    skip = pytest.mark.skip(reason="DATABASE_URL not set – database tests skipped")
    for item in items:
        if "db" in item.keywords:
            item.add_marker(skip)
