"""Time helpers: store UTC, render IST (Asia/Kolkata) – CONTRACT global conventions."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc


def utcnow() -> datetime:
    return datetime.now(UTC)


def to_utc(dt: datetime | None) -> datetime | None:
    """Naive datetimes are interpreted as UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def iso_z(dt: datetime | None) -> str | None:
    """ISO-8601 UTC with millisecond precision and trailing Z."""
    if dt is None:
        return None
    dt = to_utc(dt)
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_iso(value: str | datetime | None) -> datetime | None:
    """Accept any ISO-8601 string (Z, offset, or naive = UTC)."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return to_utc(value)
    s = str(value).strip()
    if s.endswith("Z") or s.endswith("z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        # tolerate a space instead of T and missing seconds
        s2 = s.replace(" ", "T")
        dt = datetime.fromisoformat(s2)
    return to_utc(dt)


def fmt_ist(dt: datetime | None, with_suffix: bool = True) -> str:
    """`04 Sep 2026, 15:45:30 IST`."""
    if dt is None:
        return ""
    local = to_utc(dt).astimezone(IST)
    s = local.strftime("%d %b %Y, %H:%M:%S")
    return f"{s} IST" if with_suffix else s


def fmt_ist_short(dt: datetime | None) -> str:
    """`04 Sep, 15:45:30`."""
    if dt is None:
        return ""
    return to_utc(dt).astimezone(IST).strftime("%d %b, %H:%M:%S")


def fmt_ist_time(dt: datetime | None) -> str:
    """`15:45:29 IST` (notification bodies)."""
    if dt is None:
        return ""
    return to_utc(dt).astimezone(IST).strftime("%H:%M:%S") + " IST"


def ist_stamp(dt: datetime | None = None) -> str:
    """`YYYYMMDD_HHMMSS` in IST for file names."""
    dt = dt or utcnow()
    return to_utc(dt).astimezone(IST).strftime("%Y%m%d_%H%M%S")


def ist_stamp_short(dt: datetime | None = None) -> str:
    """`YYYYMMDD_HHMM` in IST for download file names."""
    dt = dt or utcnow()
    return to_utc(dt).astimezone(IST).strftime("%Y%m%d_%H%M")


def utc_date_dir(dt: datetime | None = None) -> str:
    """UTC `YYYY-MM-DD` directory component (CONTRACT §10.1)."""
    dt = dt or utcnow()
    return to_utc(dt).strftime("%Y-%m-%d")


def label_ist(dt: datetime, bucket: str) -> str:
    local = to_utc(dt).astimezone(IST)
    return local.strftime("%d %b %H:%M") if bucket == "hour" else local.strftime("%d %b")


def epoch_ms(dt: datetime) -> int:
    return int(to_utc(dt).timestamp() * 1000)


def parse_date(value: str | date | None) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value).strip()[:10])


def hours_ago(hours: float) -> datetime:
    return utcnow() - timedelta(hours=hours)
