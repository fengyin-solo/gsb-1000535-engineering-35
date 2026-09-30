"""版本化种子数据与迁移。

- v1：仓库初始化时的内置示例（即 app/seed.py 的 SEED_ROWS），原始批次
  固定为 seed-v1，环境转换或流水线重跑都不改写它；
- v2：迁移 0002 给既有示例补录入时间戳（业务时区），批次仍记 seed-v1，
  证明“既有示例数据按原批次保留”；
- v3：迁移 0003 派生关联参考码，同样保留原批次。

迁移逐版本检查点化、幂等：目标版本内容只取决于上一版本、迁移定义与业务
时区，重复执行得到相同结果，失败可从断点继续，也可整体回滚。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.pipeline.checksum import clone_rows, digest_payload
from app.pipeline.constants import MODULES
from app.pipeline.time_utils import business_day_start, date_str, iso, parse_business_date

SEED_VERSIONS = ["v1", "v2", "v3"]
V1_BATCH_ID = "seed-v1"
DEFAULT_TZ = "Asia/Shanghai"


def v1_seed() -> dict[str, list[dict[str, Any]]]:
    """v1 内置示例：延迟导入，避免把大字典带进模块初始化。"""
    from app.seed import SEED_ROWS

    return {name: clone_rows(rows) for name, rows in SEED_ROWS.items()}


def _stamp_v1(tables: dict[str, list[dict[str, Any]]]) -> None:
    """给既有示例打上原批次标记。数据列一字段不动。"""
    for module, rows in tables.items():
        date_field = MODULES[module].date_fields[0] if MODULES[module].date_fields else None
        for index, row in enumerate(rows, start=1):
            row.setdefault("_batch_id", V1_BATCH_ID)
            row.setdefault("_seed_version", "v1")
            row.setdefault("_entry_class", "legacy")
            row.setdefault("_data_date", str(row.get(date_field, "")) if date_field else None)
            row.setdefault("_business_date", "2026-09-03")
            row.setdefault("_recorded_at", None)
            row.setdefault("_seed_seq", index)


def migrate_v1_to_v2(prev: dict[str, list[dict[str, Any]]],
                     timezone_name: str = DEFAULT_TZ) -> dict[str, list[dict[str, Any]]]:
    """迁移 0002：按业务时区给既有示例补登记时间戳，业务数据不变。"""
    tables = clone_rows(prev)
    base = parse_business_date("2026-09-03")
    for rows in tables.values():
        for index, row in enumerate(rows, start=1):
            # 登记时刻固定在业务时区 09:00 起按序号排开，保证确定性与可复现
            recorded = business_day_start(base, timezone_name).replace(hour=9, minute=(index - 1) * 5)
            row["_seed_version"] = "v2"
            row["_recorded_at"] = iso(recorded, timezone_name)
            if not row.get("_data_date"):
                row["_data_date"] = date_str(base)
    return tables


def migrate_v2_to_v3(prev: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    """迁移 0003：派生确定性关联参考码，供模块间台账互查。"""
    tables = clone_rows(prev)
    for module, rows in tables.items():
        prefix = MODULES[module].prefix
        for row in rows:
            row["_seed_version"] = "v3"
            row["_ref_code"] = f"REF-{prefix}-{int(row['id']):04d}"
    return tables


@dataclass(frozen=True)
class Migration:
    version: str
    from_version: str
    description: str
    apply: Callable[..., dict[str, list[dict[str, Any]]]]


MIGRATIONS: list[Migration] = [
    Migration("v2", "v1", "按业务时区为既有示例补登记时间戳（原批次保留）", migrate_v1_to_v2),
    Migration("v3", "v2", "派生确定性关联参考码（原批次保留）", migrate_v2_to_v3),
]
MIGRATION_BY_VERSION = {m.version: m for m in MIGRATIONS}


class SeedRegistry:
    """按需构造各版本种子；内容是 (版本, 业务时区) 的纯函数。"""

    def __init__(self) -> None:
        self._cache: dict[str, dict[str, list[dict[str, Any]]]] = {}
        self._checksums: dict[str, str] = {}

    def version(self, target: str, timezone_name: str = DEFAULT_TZ
                ) -> dict[str, list[dict[str, Any]]]:
        if target not in SEED_VERSIONS:
            raise ValueError(f"未知种子版本：{target}，可选 {SEED_VERSIONS}")
        key = f"{target}@{timezone_name}"
        if key not in self._cache:
            tables = v1_seed()
            _stamp_v1(tables)
            self._cache[f"v1@{timezone_name}"] = tables
            current = "v1"
            for migration in MIGRATIONS:
                source = self._cache[f"{current}@{timezone_name}"]
                if migration.version == "v2":
                    nxt = migration.apply(source, timezone_name)
                else:
                    nxt = migration.apply(source)
                current = migration.version
                self._cache[f"{current}@{timezone_name}"] = nxt
                if current == target:
                    break
        return clone_rows(self._cache[key])

    def checksum(self, target: str, timezone_name: str = DEFAULT_TZ) -> str:
        key = f"{target}@{timezone_name}"
        if key not in self._checksums:
            self._checksums[key] = digest_payload(self.version(target, timezone_name))
        return self._checksums[key]


seed_registry = SeedRegistry()
