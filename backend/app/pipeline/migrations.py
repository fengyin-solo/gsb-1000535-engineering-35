"""种子数据迁移：把内置示例数据演进到当前批次口径。

迁移是「有序、确定性、可重放」的纯函数：输入工作集，输出工作集。
每个迁移自带版本号，只在 applied.json 里登记一次；重复执行流水线时
已经应用过的迁移会被跳过（幂等），但工作集仍由全部迁移重放产生，
所以既有示例数据始终带着原始批次血缘，不会被新批次改写。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date as date_cls
from typing import Any

from app import clock
from app.modules import MODULES

SEED_BATCH_PREFIX = "seed"


def seed_batch_id(serial: int) -> str:
    """既有示例数据的原批次号：按其在模块内的顺序固化，永不被新批次覆盖。"""
    return f"{SEED_BATCH_PREFIX}-v{serial:04d}"


@dataclass(frozen=True)
class Migration:
    id: str
    description: str
    apply: Callable[[dict[str, list[dict[str, Any]]], str], dict[str, list[dict[str, Any]]]]


def _migrate_0001_lineage(tables: dict[str, list[dict[str, Any]]], _run_id: str) -> dict[str, list[dict[str, Any]]]:
    """给示例数据补批次血缘：batch_id 固定为 seed-v0001，记录时间统一为样例日期业务时区 08:00。

    已有 batch_id 的行保持不变（既有示例数据按原批次保留）。
    """
    for spec in MODULES:
        rows = tables.get(spec.name, [])
        for row in rows:
            if not row.get("batch_id"):
                day_text = str(row.get(spec.date_field) or "")
                day = None
                try:
                    day = date_cls.fromisoformat(day_text)
                except ValueError:
                    day = None
                row["batch_id"] = seed_batch_id(1)
                row["record_type"] = "seed"
                row["recorded_at"] = clock.iso_ts(clock.local_dt(day, 8, 0)) if day else None
                row["business_date"] = day_text or None
                row["late_entry"] = False
    return tables


def _migrate_0002_backfill_flag(tables: dict[str, list[dict[str, Any]]], _run_id: str) -> dict[str, list[dict[str, Any]]]:
    """补充补录标记字段：示例数据默认非跨日补录，供对账阶段区分两类血缘。"""
    for spec in MODULES:
        for row in tables.get(spec.name, []):
            row.setdefault("late_entry", False)
            row.setdefault("record_type", "seed")
    return tables


MIGRATIONS: tuple[Migration, ...] = (
    Migration("0001_seed_lineage", "为示例数据登记原始批次与业务时间", _migrate_0001_lineage),
    Migration("0002_backfill_flag", "补齐跨日补录标记字段", _migrate_0002_backfill_flag),
)


def pending(applied_ids: set[str]) -> list[Migration]:
    return [migration for migration in MIGRATIONS if migration.id not in applied_ids]
