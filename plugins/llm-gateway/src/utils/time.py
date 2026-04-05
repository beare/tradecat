from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Now:
    ts_utc: str
    ts_bj: str


def now_utc_bj() -> Now:
    utc = datetime.now(timezone.utc)
    bj = utc.astimezone(ZoneInfo("Asia/Shanghai"))
    return Now(
        ts_utc=utc.isoformat(timespec="seconds"),
        ts_bj=bj.isoformat(timespec="seconds"),
    )

