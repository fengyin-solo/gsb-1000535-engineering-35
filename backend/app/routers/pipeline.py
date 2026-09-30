"""流水线接口：批次状态、偏离清单、模块台账核对戳、手动触发。

- GET  /api/pipeline/status      流水线/当前批次/各阶段检查点
- GET  /api/deviations           偏离清单（跨日补录等），与台账同批次
- GET  /api/ledger               18 个模块台账核对戳汇总
- GET  /api/ledger/{module}      单模块台账（带核对戳）
- POST /api/pipeline/deploy      触发一次流水线（幂等，已完成阶段跳过）
- POST /api/pipeline/reconcile   重新核对并驱动汇总统计重算
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.pipeline import state as state_mod
from app.pipeline.constants import MODULE_ORDER, MODULES
from app.pipeline.runner import (
    BlockedByDependency,
    PipelineError,
    PipelineRunner,
    status_snapshot,
)
from app.store import store

router = APIRouter(prefix="/api", tags=["流水线"])


class DeployPayload(BaseModel):
    business_date: str = Field(..., description="业务日期 YYYY-MM-DD（按业务时区切日）")
    seed_version: str | None = None
    timezone_name: str | None = None
    force: bool = False


@router.get("/pipeline/status")
def pipeline_status() -> dict[str, Any]:
    return status_snapshot()


@router.get("/deviations")
def list_deviations(module: str | None = None) -> dict[str, Any]:
    doc = store.deviations()
    items = doc.get("items", [])
    if module:
        items = [item for item in items if item.get("module") == module]
    return {**doc, "items": items, "filtered_total": len(items)}


@router.get("/ledger")
def ledger_overview() -> dict[str, Any]:
    """各模块台账的同一批次核对戳一览。"""
    ledgers = state_mod.read_json(state_mod.LEDGER_FILE, {})
    stamps = ledgers.get("stamps", {}) if isinstance(ledgers, dict) else {}
    return {
        "batch_id": ledgers.get("_batch_id"),
        "reconciliation_id": ledgers.get("_reconciliation_id"),
        "reconciled_at": ledgers.get("_reconciled_at"),
        "modules": [
            {
                "module": module,
                "label": MODULES[module].label,
                "total": len(store.rows(module)),
                "stamp": stamps.get(module),
            }
            for module in MODULE_ORDER
        ],
    }


@router.get("/ledger/{module}")
def module_ledger(module: str) -> dict[str, Any]:
    if module not in MODULES:
        raise HTTPException(status_code=404, detail=f"模块 {module} 不存在")
    return store.ledger(module)


@router.post("/pipeline/deploy")
def deploy(payload: DeployPayload) -> dict[str, Any]:
    runner = PipelineRunner(
        business_date=payload.business_date,
        seed_version=payload.seed_version,
        timezone_name=payload.timezone_name,
    )
    try:
        record = runner.run(force=payload.force)
    except BlockedByDependency as exc:
        raise HTTPException(status_code=503, detail=f"发布被依赖阻断：{exc}") from exc
    except PipelineError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    store.reload()
    return {
        "ok": True,
        "batch_id": runner.batch_id,
        "ready": record.get("ready", False),
        "stages": record["stages"],
    }


@router.post("/pipeline/reconcile")
def reconcile_now() -> dict[str, Any]:
    """对当前批次重新核对并驱动汇总统计重算（幂等）。"""
    from app.pipeline import state as state_mod
    from app.pipeline.reconcile import run_reconcile
    from app.pipeline.samples import generate_samples

    state = state_mod.load_state()
    if not state.get("current_batch_id"):
        raise HTTPException(status_code=409, detail="尚无已发布批次，无法核对")
    _batch_id, _tables, deviations = generate_samples(
        state["seed_version"], state["business_date"], state["timezone"]
    )
    outcome = run_reconcile(
        batch_id=state["current_batch_id"],
        seed_version=state["seed_version"],
        business_date=state["business_date"],
        timezone_name=state["timezone"],
        generated_deviations=deviations,
    )
    store.reload()
    return {"ok": True, "reconciliation": outcome["reconciliation"]}
