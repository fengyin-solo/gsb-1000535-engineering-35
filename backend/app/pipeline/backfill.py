"""跨日补录样例生成：按固定业务时区制造“业务日已切换后补录前一日数据”的样例。

跨日的含义全部以 BUSINESS_TZ 为准：

* 业务发生时间：前一业务日 23:50（业务时区）；
* 实际补录时间：当前业务日 00:10（业务时区）——日期已跨过午夜；
* 若换算环境的宿主时区不同（如容器是 UTC、业务时区是 +08），两个时间戳的
  UTC 表达会落在不同 UTC 日，但业务日归属不变，这正是“环境转换以业务时区为准”。

生成是确定性的：同一批次号、同样的工作集，永远产出同样的行；重跑时按
（模块、业务编号、批次号）去重，不会重复插入污染台账。
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from app import clock
from app.modules import MODULES

BACKFILL_RECORD_TYPE = "backfill"
BACKFILL_SUFFIX = "跨日补录"


def _next_id(rows: list[dict[str, Any]]) -> int:
    return max((int(row.get("id", 0)) for row in rows), default=0) + 1


def _business_code(spec, index: int) -> str:
    return f"{spec.code_prefix}-X{index:04d}"


def enrich_backfill(
    tables: dict[str, list[dict[str, Any]]],
    batch_id: str,
    business_day: clock.date,
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    """在工作集上补入跨日补录行，返回新工作集与新增行清单。

    幂等：若同批次同业务编号的补录行已存在，保留原行，不重复插入。
    """
    result = deepcopy(tables)
    created: list[dict[str, Any]] = []
    previous = clock.previous_day(business_day)

    for spec in MODULES:
        rows = result.setdefault(spec.name, [])
        # 该模块本批次已有的补录序号，保证重跑不会再造一批
        existing_codes = {
            str(row.get(spec.required_fields[0]))
            for row in rows
            if row.get("batch_id") == batch_id and row.get("record_type") == BACKFILL_RECORD_TYPE
        }
        index = 1
        code = _business_code(spec, index)
        if code in existing_codes:
            continue
        occurred_at = clock.local_dt(previous, 23, 50)
        entered_at = clock.local_dt(business_day, 0, 10)
        row: dict[str, Any] = {
            "id": _next_id(rows),
            "status": spec.statuses[1] if len(spec.statuses) > 1 else spec.statuses[0],
            "pending": True,
            "abnormal": True,  # 跨日补录默认进入偏离清单待核
            # 业务列：必填列给编号/补录说明，其余列给带模块名的说明
            spec.required_fields[0]: code,
        }
        for field_name in spec.required_fields[1:]:
            row[field_name] = f"{spec.label}{BACKFILL_SUFFIX}·{field_name}"
        for field_name in spec.all_fields:
            row.setdefault(field_name, f"{spec.label}{BACKFILL_SUFFIX}样例")
        # 业务日期字段：跨日场景下列里登记的是“实际发生”的前一业务日
        row[spec.date_field] = clock.iso(previous)
        row.update({
            "batch_id": batch_id,
            "record_type": BACKFILL_RECORD_TYPE,
            "business_date": clock.iso(previous),
            "occurred_at": clock.iso_ts(occurred_at),
            "recorded_at": clock.iso_ts(entered_at),
            "entered_at": clock.iso_ts(entered_at),
            "late_entry": True,
            "late_minutes": int((entered_at - occurred_at).total_seconds() // 60),
            "tz": f"BUSINESS_TZ={clock.settings.business_tz}",
            "note": f"业务发生于{clock.iso(previous)} 23:50，{clock.iso(business_day)} 00:10 跨日补录",
        })
        rows.append(row)
        created.append({"module": spec.name, "id": row["id"], "code": code,
                        "business_date": clock.iso(previous), "recorded_at": row["recorded_at"]})
    return result, created
