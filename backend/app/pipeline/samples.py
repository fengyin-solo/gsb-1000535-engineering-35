"""按业务时区生成跨日补录样例。

每个模块在指定业务日 D 固定生成两条样例：
  1. regular    当日样例：数据发生在 D、在 D 登记；
  2. late_entry 跨日补录：数据发生在前一自然日 D-1（业务时区切日），
     到 D 才补录登记。这类样例计入偏离清单。

生成结果只取决于（种子版本、业务日期、业务时区、模块元数据），
重复执行得到同批次同内容，不会产生重复数据。
"""
from __future__ import annotations

from typing import Any

from app.pipeline.checksum import make_batch_id, stamp_rows
from app.pipeline.constants import MODULES, MODULE_ORDER
from app.pipeline.time_utils import (
    business_day_start,
    date_str,
    iso,
    parse_business_date,
    previous_day,
)


def _fill_row(meta: object, code: str, day: str) -> dict[str, str]:
    """业务列填充：编号列为 code，日期列取 day，其余为确定性占位内容。"""
    values: dict[str, str] = {}
    for column in meta.fields:
        if column == meta.code_field:
            values[column] = code
        elif column in meta.date_fields:
            values[column] = day
        else:
            values[column] = f"{meta.label}{column}样例({day})"
    return values


def _build_row(
    *,
    module: str,
    entry_id: int,
    entry_class: str,
    business_date: str,
    data_day: str,
    recorded_at: str,
    status: str,
    abnormal: bool,
    batch_id: str,
    seed_version: str,
) -> dict[str, Any]:
    meta = MODULES[module]
    code = f"{meta.prefix}-{business_date.replace('-', '')}-{'L' if entry_class == 'late_entry' else 'R'}"
    row: dict[str, Any] = {
        "id": entry_id,
        "status": status,
        "pending": entry_class == "late_entry",
        "abnormal": abnormal,
        **_fill_row(meta, code, data_day),
        "_batch_id": batch_id,
        "_seed_version": seed_version,
        "_business_date": business_date,
        "_recorded_at": recorded_at,
        "_entry_class": entry_class,
        "_data_date": data_day,
    }
    return row


def generate_samples(seed_version: str, business_date_raw: str, timezone_name: str
                     ) -> tuple[str, dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    """返回 (批次ID, 各模块样例行, 偏离样例说明)。

    样例 ID 从 1001 起预留：既有种子行在 1..999，流水线样例固定在千位段，
    天然与历史批次错开，重跑按自然键（模块+编号列）幂等覆盖。
    """
    business_date = parse_business_date(business_date_raw)
    day_d = date_str(business_date)
    day_prev = date_str(previous_day(business_date))
    batch_id = make_batch_id(seed_version, day_d, timezone_name)

    day_start = business_day_start(business_date, timezone_name)
    tables: dict[str, list[dict[str, Any]]] = {}
    deviations: list[dict[str, Any]] = []

    next_id = 1001
    for order, module in enumerate(MODULE_ORDER):
        meta = MODULES[module]
        # 当日样例：业务日 08:30 登记
        regular = _build_row(
            module=module,
            entry_id=next_id,
            entry_class="regular",
            business_date=day_d,
            data_day=day_d,
            recorded_at=iso(day_start.replace(hour=8, minute=30 + order), timezone_name),
            status=meta.statuses[1] if len(meta.statuses) > 1 else meta.statuses[0],
            abnormal=False,
            batch_id=batch_id,
            seed_version=seed_version,
        )
        next_id += 1
        # 跨日补录：数据日为 D-1，业务日 09:15 补录
        late = _build_row(
            module=module,
            entry_id=next_id,
            entry_class="late_entry",
            business_date=day_d,
            data_day=day_prev,
            recorded_at=iso(day_start.replace(hour=9, minute=15 + order), timezone_name),
            status=meta.statuses[0],
            abnormal=True,
            batch_id=batch_id,
            seed_version=seed_version,
        )
        next_id += 1
        tables[module] = [regular, late]
        deviations.append({
            "module": module,
            "module_label": meta.label,
            "entry_id": late["id"],
            "code": late[meta.code_field],
            "kind": "cross_day_late_entry",
            "data_date": day_prev,
            "business_date": day_d,
            "recorded_at": late["_recorded_at"],
            "timezone": timezone_name,
            "detail": f"数据日期 {day_prev} 的记录于 {day_d} 跨日补录（{timezone_name}）",
        })

    for rows in tables.values():
        stamp_rows(rows)
    return batch_id, tables, deviations
