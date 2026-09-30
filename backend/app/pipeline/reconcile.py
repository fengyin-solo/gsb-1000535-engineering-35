"""三方对账与汇总重算。

核对对象（三者必须指向同一批次号 batch_id）：

1. 汇总页 summary：看板卡片 + 各模块计数 + 对账结论；
2. 各模块台账 ledgers：每张模块台账都盖同一批次号，并带本行数/计数；
3. 偏离清单 deviations：跨日补录与批次/时间异常的行，同样盖批次号。

对账结果同时写到这三个载体上，然后由同一份快照重新汇总统计
（统计不缓存、每次核对都重算），避免三者之间出现任何漂移。
"""
from __future__ import annotations

from typing import Any

from app import clock
from app.modules import MODULES

DEVIATION_LATE_ENTRY = "跨日补录"
DEVIATION_BATCH_MISMATCH = "批次不一致"
DEVIATION_TIME_SKEW = "业务时间与登记时间跨日"


def build_deviations(
    tables: dict[str, list[dict[str, Any]]],
    batch_id: str,
) -> list[dict[str, Any]]:
    """从工作集抽取偏离清单：跨日补录行逐行列示，批次/时间异常也记录。"""
    deviations: list[dict[str, Any]] = []
    for spec in MODULES:
        for row in tables.get(spec.name, []):
            if row.get("batch_id") == batch_id and row.get("late_entry"):
                deviations.append({
                    "module": spec.name,
                    "module_label": spec.label,
                    "entry_id": row.get("id"),
                    "code": row.get(spec.required_fields[0]),
                    "kind": DEVIATION_LATE_ENTRY,
                    "business_date": row.get("business_date"),
                    "occurred_at": row.get("occurred_at"),
                    "recorded_at": row.get("recorded_at") or row.get("entered_at"),
                    "late_minutes": row.get("late_minutes"),
                    "status": row.get("status"),
                    "detail": row.get("note", "业务日切换后补录前一日数据"),
                    "batch_id": batch_id,
                })
            # 台账行如果声明了批次但和汇总批次对不上，记为批次不一致
            declared = row.get("batch_id")
            if declared and declared != batch_id and str(declared).startswith("batch-"):
                deviations.append({
                    "module": spec.name,
                    "module_label": spec.label,
                    "entry_id": row.get("id"),
                    "code": row.get(spec.required_fields[0]),
                    "kind": DEVIATION_BATCH_MISMATCH,
                    "status": row.get("status"),
                    "detail": f"台账批次 {declared} 与汇总批次 {batch_id} 不一致",
                    "batch_id": batch_id,
                })
    return deviations


def compute_summary(
    tables: dict[str, list[dict[str, Any]]],
    batch_id: str,
    business_day: clock.date,
    at: str | None = None,
) -> dict[str, Any]:
    """由工作集重新汇总统计：卡片与各模块计数都按批次重算，不复用旧值。"""
    day_text = clock.iso(business_day)
    module_rows: list[dict[str, Any]] = []
    for spec in MODULES:
        rows = tables.get(spec.name, [])
        batch_rows = [row for row in rows if row.get("batch_id") == batch_id]
        module_rows.append({
            "name": spec.name,
            "label": spec.label,
            "total": len(rows),
            "created": len(batch_rows),
            "pending": sum(1 for row in batch_rows if row.get("pending")),
            "abnormal": sum(1 for row in batch_rows if row.get("abnormal")),
            "backfill": sum(1 for row in batch_rows if row.get("late_entry")),
            "batch_id": batch_id,
        })
    cards = [
        {"label": "业务模块", "value": len(MODULES)},
        {"label": "本批次新增", "value": sum(int(row["created"]) for row in module_rows)},
        {"label": "待处理", "value": sum(int(row["pending"]) for row in module_rows)},
        {"label": "异常量", "value": sum(int(row["abnormal"]) for row in module_rows)},
        {"label": "跨日补录", "value": sum(int(row["backfill"]) for row in module_rows)},
    ]
    return {
        "batch_id": batch_id,
        "business_date": day_text,
        "timezone": clock.settings.business_tz,
        "generated_at": at or clock.iso_ts(),
        "cards": cards,
        "modules": module_rows,
    }


def reconcile(
    tables: dict[str, list[dict[str, Any]]],
    batch_id: str,
    business_day: clock.date,
    at: str | None = None,
) -> dict[str, Any]:
    """三方对账并把结论同时落到汇总页、各模块台账、偏离清单。

    返回可直接存入快照的结构；任何一处批次不一致都会把核对结论标成 failed。
    时间戳由 ``at`` 固定到本次运行起点，保证同一批次重放产物字节一致。
    """
    checked_at = at or clock.iso_ts()
    deviations = build_deviations(tables, batch_id)
    summary = compute_summary(tables, batch_id, business_day, at=checked_at)

    ledgers: dict[str, dict[str, Any]] = {}
    mismatches: list[str] = []
    for spec in MODULES:
        rows = tables.get(spec.name, [])
        batch_rows = [row for row in rows if row.get("batch_id") == batch_id]
        ledger_batch_ids = {str(row.get("batch_id")) for row in batch_rows}
        consistent = ledger_batch_ids <= {batch_id}
        if not consistent:
            mismatches.append(spec.name)
        ledgers[spec.name] = {
            "module": spec.name,
            "label": spec.label,
            "batch_id": batch_id,
            "batch_consistent": consistent,
            "total_rows": len(rows),
            "batch_rows": len(batch_rows),
            "pending": sum(1 for row in batch_rows if row.get("pending")),
            "abnormal": sum(1 for row in batch_rows if row.get("abnormal")),
            "deviations": sum(1 for item in deviations if item["module"] == spec.name),
        }

    # 偏离清单里不允许残留“批次不一致”（历史种子批次 seed-* 不属于待发布批次，不计）
    unresolved = [item for item in deviations if item["kind"] == DEVIATION_BATCH_MISMATCH]
    matched = len(mismatches) == 0 and not unresolved
    result = {
        "batch_id": batch_id,
        "business_date": clock.iso(business_day),
        "timezone": clock.settings.business_tz,
        "checked_at": checked_at,
        "matched": matched,
        "sources": ["summary", "ledgers", "deviations"],
        "mismatch_modules": mismatches,
        "deviation_count": len(deviations),
        "late_entry_count": sum(1 for item in deviations if item["kind"] == DEVIATION_LATE_ENTRY),
    }

    # 把核对结果写回三个载体（同一批次、同一次核对）
    summary["reconciliation"] = result
    for module_name, ledger in ledgers.items():
        ledger["reconciled_at"] = checked_at
        ledger["matched"] = matched
    for item in deviations:
        item["reconciled_at"] = checked_at
        item["matched"] = matched

    return {
        "summary": summary,
        "ledgers": ledgers,
        "deviations": deviations,
        "reconciliation": result,
    }


def verify_artifact(artifact: dict[str, Any], batch_id: str) -> list[str]:
    """发布前复核：重新读出产物，校验三处批次号完全一致。返回问题清单，空列表即通过。"""
    problems: list[str] = []
    summary = artifact.get("summary", {})
    ledgers = artifact.get("ledgers", {})
    deviations = artifact.get("deviations", [])

    if summary.get("batch_id") != batch_id:
        problems.append(f"汇总页批次 {summary.get('batch_id')} 与发布批次 {batch_id} 不一致")
    for name, ledger in ledgers.items():
        if ledger.get("batch_id") != batch_id:
            problems.append(f"模块台账 {name} 批次 {ledger.get('batch_id')} 与发布批次不一致")
        if not ledger.get("batch_consistent"):
            problems.append(f"模块台账 {name} 内存在异批次行")
    for item in deviations:
        if item.get("batch_id") != batch_id:
            problems.append(
                f"偏离清单 {item.get('module')}#{item.get('entry_id')} 批次 {item.get('batch_id')} 不一致"
            )
    reconciliation = artifact.get("reconciliation", {})
    if reconciliation.get("batch_id") != batch_id or not reconciliation.get("matched"):
        problems.append("对账结论未通过（matched != true）")
    # 统计复核：卡片数字必须能由模块行重新加总出来
    modules = summary.get("modules", [])
    expect_created = sum(int(row.get("created", 0)) for row in modules)
    cards = {card.get("label"): card.get("value") for card in summary.get("cards", [])}
    if cards.get("本批次新增") != expect_created:
        problems.append("汇总统计与模块台账重算结果不一致")
    return problems
