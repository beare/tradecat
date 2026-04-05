"""官方历史归档源 backfill 计划工具（daily/monthly 智能边界）。

说明：
- daily 与 monthly 的数据本质相同；如果两者都导入，会造成重复写入压力。
- 规则：
  - month 完整覆盖（且不是当前月）→ 优先 monthly（一个月一个文件）
  - 边界月/当前月/禁用 monthly → 按日 daily
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, timezone


@dataclass(frozen=True)
class PlanItem:
    symbol: str
    kind: str  # "daily" | "monthly"
    period: str  # YYYY-MM 或 YYYY-MM-DD
    start_date: date
    end_date: date


def _month_start(d: date) -> date:
    return date(d.year, d.month, 1)


def _month_end(d: date) -> date:
    last = calendar.monthrange(d.year, d.month)[1]
    return date(d.year, d.month, last)


def _iter_months(start: date, end: date) -> list[date]:
    cur = _month_start(start)
    last = _month_start(end)
    out: list[date] = []
    while cur <= last:
        out.append(cur)
        if cur.month == 12:
            cur = date(cur.year + 1, 1, 1)
        else:
            cur = date(cur.year, cur.month + 1, 1)
    return out


def build_plan(symbol: str, start_date: date, end_date: date, *, prefer_monthly: bool) -> list[PlanItem]:
    if start_date > end_date:
        raise ValueError("start_date 不能大于 end_date")

    today_utc = datetime.now(tz=timezone.utc).date()
    current_month = _month_start(today_utc)

    items: list[PlanItem] = []
    for m in _iter_months(start_date, end_date):
        m_start = m
        m_end = _month_end(m)
        want_start = max(start_date, m_start)
        want_end = min(end_date, m_end)

        full_month = want_start == m_start and want_end == m_end
        if not prefer_monthly or not full_month or m == current_month:
            d = want_start
            while d <= want_end:
                items.append(
                    PlanItem(
                        symbol=symbol,
                        kind="daily",
                        period=f"{d:%Y-%m-%d}",
                        start_date=d,
                        end_date=d,
                    )
                )
                d = date.fromordinal(d.toordinal() + 1)
            continue

        items.append(
            PlanItem(
                symbol=symbol,
                kind="monthly",
                period=f"{m_start:%Y-%m}",
                start_date=want_start,
                end_date=want_end,
            )
        )

    return items


__all__ = ["PlanItem", "build_plan"]

