"""流水线接口：发布批次、对账结论、模块台账、偏离清单的只读查询。

这些接口让前端/运维直接核对“汇总页、各模块台账、偏离清单”是否同批，
也供部署探针判断服务是否 ready（未成功发布的批次不算就绪）。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app import clock
from app.config import settings
from app.modules import MODULES
from app.pipeline.state import PipelineState
from app.store import store

router = APIRouter(prefix="/api/pipeline", tags=["流水线"])


def _state() -> PipelineState:
    """按当前 settings.state_dir 构造，保证环境切换/测试改目录后立即生效。"""
    return PipelineState()


def _current_batch_id() -> str | None:
    release = _state().load_release()
    return str(release["batch_id"]) if release else None


@router.get("/status")
def pipeline_status() -> dict[str, Any]:
    """当前业务批次的检查点、就绪状态与发布指针。"""
    batch_id = clock.batch_id_for(clock.today())
    run = _state().load_run(batch_id)
    release = _state().load_release()
    return {
        "batch_id": batch_id,
        "business_date": clock.iso(clock.today()),
        "timezone": settings.business_tz,
        "run_status": run.get("status"),
        "checkpoint": run.get("checkpoint"),
        "ready": store.ready and release is not None,
        "stages": run.get("stages", {}),
        "release": release,
        "migrations": _state().applied_migrations(),
    }


@router.get("/releases")
def release_history() -> dict[str, Any]:
    """发布历史（包含回滚记录），用于版本追溯。"""
    st = _state()
    return {"items": st.release_history(), "current": st.load_release()}


@router.get("/deviations")
def deviations(batch_id: str | None = None) -> dict[str, Any]:
    """偏离清单：默认读当前发布批次，与汇总页、模块台账同源。"""
    target = batch_id or _current_batch_id()
    if not target:
        return {"batch_id": None, "items": [], "total": 0, "ready": False}
    snapshot = _state().load_snapshot(target)
    if not snapshot:
        return {"batch_id": target, "items": [], "total": 0, "ready": False}
    items = snapshot.get("artifact", {}).get("deviations", [])
    return {
        "batch_id": target,
        "business_date": snapshot.get("business_date"),
        "timezone": snapshot.get("timezone"),
        "items": items,
        "total": len(items),
        "ready": True,
    }


@router.get("/ledgers")
def ledgers(batch_id: str | None = None) -> dict[str, Any]:
    """各模块台账批次戳与计数：逐项可核对 batch_consistent。"""
    target = batch_id or _current_batch_id()
    if not target:
        return {"batch_id": None, "items": [], "ready": False}
    snapshot = _state().load_snapshot(target)
    if not snapshot:
        return {"batch_id": target, "items": [], "ready": False}
    ledgers_map = snapshot.get("artifact", {}).get("ledgers", {})
    return {
        "batch_id": target,
        "items": [ledgers_map[spec.name] for spec in MODULES if spec.name in ledgers_map],
        "ready": True,
    }
