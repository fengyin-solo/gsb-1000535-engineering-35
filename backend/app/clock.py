"""业务时钟：所有“今天/跨日/批次时间戳”统一按业务时区 BUSINESS_TZ 计算。

绝不在流水线里直接使用 naive 的 datetime.now()；环境转换时只换
BUSINESS_TZ，切日口径随之整体切换。
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.config import settings


def business_zone() -> ZoneInfo:
    return ZoneInfo(settings.business_tz)


def now() -> datetime:
    """带业务时区信息的当前时刻。"""
    return datetime.now(tz=business_zone())


def today() -> date:
    """业务时区下的今天。"""
    return now().date()


def local_dt(day: date, hour: int = 0, minute: int = 0, second: int = 0) -> datetime:
    """把业务时区下的某日某时刻构造成带时区信息的 datetime。"""
    return datetime.combine(day, time(hour, minute, second), tzinfo=business_zone())


def iso(day: date) -> str:
    return day.isoformat()


def previous_day(day: date) -> date:
    return day - timedelta(days=1)


def iso_ts(moment: datetime | None = None) -> str:
    """带时区偏移的 ISO-8601 时间戳（如 2026-09-30T08:00:00+08:00）。"""
    return (moment or now()).isoformat(timespec="seconds")


def batch_id_for(day: date) -> str:
    """业务日期 -> 固定批次号：同一业务日期重跑永远落同一批次（幂等基石）。"""
    return f"batch-{iso(day)}"
