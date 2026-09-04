"""CLI: `python -m app.jobs.retention --dry-run | --run` (CONTRACT §13.3 check 26)."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from app.core.logging import setup_logging
from app.db.session import SessionLocal, dispose
from app.services import settings_service as cfg
from app.services.retention_job import run_retention


async def _main(dry_run: bool) -> int:
    async with SessionLocal() as db:
        await cfg.load(db)
    counts = await run_retention(dry_run=dry_run)
    print(json.dumps(counts, indent=2))
    await dispose()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Sentinel Gujarat retention purge")
    g = parser.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="count only, delete nothing, no audit row")
    g.add_argument("--run", action="store_true", help="delete expired data and write audit retention.purge")
    args = parser.parse_args()
    setup_logging()
    dry = not args.run
    sys.exit(asyncio.run(_main(dry)))


if __name__ == "__main__":
    main()
