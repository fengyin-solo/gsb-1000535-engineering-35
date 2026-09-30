"""业务时区与跨日口径。

业务日期（business_date）是业务时区下的自然日；样例的发生时间（data_date）
与登记时间（recorded_at）都在业务时区取时刻，再以 ISO-8601 带偏移量的形式
落盘。环境转换时只认 BUSINESS_TIMEZONE（或调用方显式传入的时区），
不读宿主机/容器的 TZ。
"""
from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.config import settings


def resolve_tz(timezone_name: str | None = None) -> ZoneInfo:
    return ZoneInfo(timezone_name or os.environ.get("BUSINESS_TIMEZONE")
                    or settings.business_timezone)


def business_tz() -> ZoneInfo:
    return resolve_tz(None)


def now_local(timezone_name: str | None = None) -> datetime:
    return datetime.now(resolve_tz(timezone_name))


def parse_business_date(raw: str | date) -> date:
    if isinstance(raw, date):
        return raw
    return datetime.strptime(raw.strip(), "%Y-%m-%d").date()


def business_day_start(day: date, timezone_name: str | None = None) -> datetime:
    return datetime.combine(day, time(hour=0, minute=0), tzinfo=resolve_tz(timezone_name))


def iso(dt: datetime, timezone_name: str | None = None) -> str:
    """带时区偏移的时间字符串，如 2026-09-30T08:30:00+08:00。"""
    return dt.astimezone(resolve_tz(timezone_name)).isoformat(timespec="seconds")


def date_str(day: date) -> str:
    return day.isoformat()


def previous_day(day: date) -> date:
    return day - timedelta(days=1)
