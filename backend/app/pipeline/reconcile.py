"""核对（reconcile）：校验同一批次并把核对结果落到三件套。

同一份核对结论（reconciliation_id + 批次 ID + 各模块校验和）必须同时落到：
  1. 汇总页 summary.json；
  2. 各模块台账 ledgers.json 的 stamps；
  3. 偏离清单 deviations.json。
核对完成后驱动汇总统计重算（当前批次口径），结果写缓存，
verify/publish 以缓存与落盘快照为准。
"""
from __future__ import annotations

from typing import Any

from app.pipeline import state as state_mod
from app.pipeline.checksum import digest_payload, table_checksum
from app.pipeline.constants import MODULES, MODULE_ORDER
from app.pipeline.time_utils import iso, now_local


def recompute_aggregate(batch_id: str,
                        current_tables: dict[str, list[dict[str, Any]]],
                        all_tables: dict[str, list[dict[str, Any]]],
                        timezone_name: str | None = None) -> dict[str, Any]:
    """重算汇总统计：本批新增为主卡片，台账总量为辅助列。"""
    module_rows: list[dict[str, Any]] = []
    for module in MODULE_ORDER:
        rows = current_tables.get(module, [])
        module_rows.append({
            "name": module,
            "label": MODULES[module].label,
            "created": len(rows),
            "pending": sum(1 for row in rows if row.get("pending")),
            "abnormal": sum(1 for row in rows if row.get("abnormal")),
            "ledger_total": len(all_tables.get(module, [])),
            "checksum": table_checksum(rows),
        })
    cards = [
        {"label": "业务模块", "value": len(module_rows)},
        {"label": "本批新增", "value": sum(int(item["created"]) for item in module_rows)},
        {"label": "待处理（本批）", "value": sum(int(item["pending"]) for item in module_rows)},
        {"label": "异常量（本批）", "value": sum(int(item["abnormal"]) for item in module_rows)},
        {"label": "台账总量（含既有批次）",
         "value": sum(int(item["ledger_total"]) for item in module_rows)},
    ]
    return {"batch_id": batch_id, "cards": cards, "modules": module_rows,
            "computed_at": iso(now_local(timezone_name), timezone_name)}


def run_reconcile(*, batch_id: str, seed_version: str, business_date: str,
                  timezone_name: str, generated_deviations: list[dict[str, Any]]
                  ) -> dict[str, Any]:
    """核对当前批次并把同一份结论写入台账、偏离清单、汇总页。"""
    reconciliation_id = f"REC-{batch_id}"
    reconciled_at = iso(now_local(timezone_name), timezone_name)

    ledger_file = state_mod.read_json(state_mod.LEDGER_FILE, {})
    raw_tables = ledger_file.get("tables", ledger_file)
    stamps: dict[str, dict[str, Any]] = {}
    current_tables: dict[str, list[dict[str, Any]]] = {}
    all_tables: dict[str, list[dict[str, Any]]] = {}
    all_ok = True

    for module in MODULE_ORDER:
        rows = raw_tables.get(module, [])
        batch_rows = [row for row in rows if row.get("_batch_id") == batch_id]
        current_tables[module] = batch_rows
        all_tables[module] = rows
        abnormal_in_ledger = sum(1 for row in batch_rows if row.get("abnormal"))
        stamp = {
            "batch_id": batch_id,
            "reconciliation_id": reconciliation_id,
            "module": module,
            "module_label": MODULES[module].label,
            "created": len(batch_rows),
            "pending": sum(1 for row in batch_rows if row.get("pending")),
            "abnormal": abnormal_in_ledger,
            "ledger_total": len(rows),
            "checksum": table_checksum(batch_rows),
            "reconciled_at": reconciled_at,
        }
        stamps[module] = stamp
        if len(batch_rows) != 2:  # 每模块固定 1 当日 + 1 跨日补录
            all_ok = False

    # 偏离清单：生成阶段给出的跨日补录，逐条与台账现况对齐
    items: list[dict[str, Any]] = []
    for dev in generated_deviations:
        module = dev["module"]
        code = dev["code"]
        rows = current_tables.get(module, [])
        code_field = MODULES[module].code_field
        live = next((row for row in rows if str(row.get(code_field)) == str(code)), None)
        item = dict(dev)
        item.update({
            "batch_id": batch_id,
            "reconciliation_id": reconciliation_id,
            "present_in_ledger": live is not None,
            "entry_checksum": live.get("_checksum") if live else None,
        })
        items.append(item)
        if live is None:
            all_ok = False
    deviation_checksum = digest_payload(items)

    aggregate = recompute_aggregate(batch_id, current_tables, all_tables, timezone_name)
    module_checksums = {m: stamps[m]["checksum"] for m in MODULE_ORDER}
    ledger_checksum = digest_payload(module_checksums)
    abnormal_total = sum(stamps[m]["abnormal"] for m in MODULE_ORDER)
    if abnormal_total != len(items):
        all_ok = False  # 台账异常行数与偏离清单条数必须一致

    reconciliation = {
        "reconciliation_id": reconciliation_id,
        "batch_id": batch_id,
        "seed_version": seed_version,
        "business_date": business_date,
        "timezone": timezone_name,
        "reconciled_at": reconciled_at,
        "consistent": all_ok,
        "ledger_checksum": ledger_checksum,
        "deviation_checksum": deviation_checksum,
        "module_checksums": module_checksums,
        "deviation_count": len(items),
        "modules": [stamps[m] for m in MODULE_ORDER],
    }

    aggregate["reconciliation_id"] = reconciliation_id
    aggregate["reconciliation"] = {
        k: v for k, v in reconciliation.items() if k != "modules"
    }
    aggregate["cards"] = aggregate["cards"] + [
        {"label": "偏离条数", "value": len(items)},
    ]

    # 三件套落盘：同批次同核对号
    new_ledger_file = {
        "_batch_id": batch_id,
        "_reconciliation_id": reconciliation_id,
        "_reconciled_at": reconciled_at,
        "tables": {module: raw_tables.get(module, []) for module in MODULE_ORDER},
        "stamps": stamps,
    }
    deviation_doc = {
        "batch_id": batch_id,
        "reconciliation_id": reconciliation_id,
        "checksum": deviation_checksum,
        "reconciled_at": reconciled_at,
        "timezone": timezone_name,
        "total": len(items),
        "items": items,
    }
    state_mod.atomic_write_json(state_mod.LEDGER_FILE, new_ledger_file)
    state_mod.atomic_write_json(state_mod.DEVIATION_FILE, deviation_doc)
    state_mod.atomic_write_json(state_mod.SUMMARY_FILE, aggregate)

    from app.pipeline.adapters import get_cache_adapter

    cache = get_cache_adapter()
    cache.cache_set(f"agg:{batch_id}", aggregate)
    cache.cache_set("current", {"batch_id": batch_id, "reconciliation_id": reconciliation_id})

    return {"reconciliation": reconciliation,
            "deviation_checksum": deviation_checksum,
            "aggregate": aggregate}
