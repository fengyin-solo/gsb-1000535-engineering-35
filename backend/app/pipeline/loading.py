"""台账装载：把迁移后的既有种子行与本批样例并入活台账。

装载以队列作业为单位逐模块提交，每个作业幂等（自然键 = 模块 + 编号列
+ 行 id），重复执行只覆盖同内容行，绝不追加重复行；
既有示例数据按原批次（seed-v1）保留，不被新批次改写。
"""
from __future__ import annotations

from typing import Any

from app.pipeline import state as state_mod
from app.pipeline.checksum import stamp_rows
from app.pipeline.constants import MODULE_ORDER


def build_load_jobs(batch_id: str, generated: dict[str, list[dict[str, Any]]],
                    seed_tables: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """每个模块一个作业：既有种子行 + 本批两条样例。"""
    jobs: list[dict[str, Any]] = []
    for seq, module in enumerate(MODULE_ORDER):
        jobs.append({
            "job_id": f"load-{batch_id}-{module}",
            "batch_id": batch_id,
            "stage": "load",
            "seq": seq,
            "module": module,
            "legacy_rows": seed_tables.get(module, []),
            "sample_rows": generated.get(module, []),
            "status": "pending",
        })
    return jobs


def seed_load_jobs(batch_id: str, generated: dict[str, list[dict[str, Any]]],
                   seed_tables: dict[str, list[dict[str, Any]]]) -> int:
    """把 18 个模块的装载作业放进队列（幂等：重复播种不产生重复作业）。"""
    queue = _queue()
    for job in build_load_jobs(batch_id, generated, seed_tables):
        queue.enqueue(job)
    return len(build_load_jobs(batch_id, generated, seed_tables))


def run_load_jobs(batch_id: str) -> dict[str, int]:
    """认领并执行本批全部装载作业；失败作业复位 pending，由重跑继续。

    返回 {claimed, completed, pending}。调用方据此判断是否还有未完成阶段。
    """
    queue = _queue()
    claimed = 0
    completed = 0
    while True:
        job = queue.claim(batch_id)
        if job is None:
            break
        claimed += 1
        try:
            legacy = [dict(row) for row in job.get("legacy_rows", [])]
            samples = [dict(row) for row in job.get("sample_rows", [])]
            _apply_module_rows(job["module"], legacy, samples)
            queue.complete(job["job_id"],
                           result={"legacy_rows": len(legacy), "sample_rows": len(samples)})
            completed += 1
        except Exception as exc:  # 装载未落盘：复位作业，批次不能就绪
            queue.fail(job["job_id"], str(exc))
    return {"claimed": claimed, "completed": completed,
            "pending": queue.pending_count(batch_id)}


def _row_identity(row: dict[str, Any]) -> tuple[str, str]:
    return (str(row.get("_batch_id", "")), str(row.get("id")))


def _apply_module_rows(module: str, legacy_rows: list[dict[str, Any]],
                       sample_rows: list[dict[str, Any]]) -> None:
    """幂等 upsert：同 (批次, id) 覆盖，新行追加；其它历史批次原样保留。"""
    ledgers = state_mod.read_json(state_mod.LEDGER_FILE, {})
    if isinstance(ledgers, dict) and "tables" in ledgers:
        table = ledgers["tables"].setdefault(module, [])
    else:
        table = ledgers.setdefault(module, [])

    incoming = stamp_rows(legacy_rows + sample_rows)
    incoming_map = {_row_identity(row): row for row in incoming}

    replaced: list[dict[str, Any]] = []
    for row in table:
        identity = _row_identity(row)
        if identity in incoming_map:
            replaced.append(incoming_map.pop(identity))  # 覆盖同身份行
        else:
            replaced.append(row)
    replaced.extend(incoming_map.values())  # 剩余的都是新身份行
    replaced.sort(key=lambda row: int(row.get("id", 0)))

    if isinstance(ledgers, dict) and "tables" in ledgers:
        ledgers["tables"][module] = replaced
    else:
        ledgers[module] = replaced
    state_mod.atomic_write_json(state_mod.LEDGER_FILE, ledgers)


def _queue():
    from app.pipeline.adapters import get_queue_adapter

    return get_queue_adapter()
