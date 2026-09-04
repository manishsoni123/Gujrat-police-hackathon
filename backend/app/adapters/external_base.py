"""`ExternalLookupAdapter` – integration-readiness interface for VAHAN / SARTHI / eGujCop / AFIS / NAFIS (X1)."""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from typing import Any


class ExternalLookupAdapter(ABC):
    system: str = "external"
    adapter: str = "base"

    @abstractmethod
    async def lookup(self, key: str) -> dict[str, Any]:
        """Return a dict with at least `source`, `adapter`, `found` and `note`."""


def stable_bucket(key: str, buckets: int) -> int:
    """Deterministic 0..buckets-1 from a key (so mocks answer consistently)."""
    h = hashlib.sha256(key.encode("utf-8")).digest()
    return int.from_bytes(h[:4], "big") % buckets


def pick(key: str, salt: str, options: list[Any]) -> Any:
    return options[stable_bucket(f"{salt}:{key}", len(options))]
