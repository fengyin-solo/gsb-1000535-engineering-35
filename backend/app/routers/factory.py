"""业务模块路由工厂：18 个模块的列表/明细/登记/动作/导出结构完全一致。

批次口径在这里集中生效：

* 列表支持按 batch_id 过滤（各模块台账可只看同一批次）；
* 所有列表/导出响应都盖当前发布批次与对账时间，前端台账页直接展示，
  保证“看到的台账”和汇总页、偏离清单来自同一批次；
* /export 定义在 /{entry_id} 之前，避免被路径参数吞掉。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.modules import REGISTRY, ModuleSpec
from app.schemas import ActionResult, EntryPayload
from app.services.base import BaseService
from app.store import store

# 每个模块的 service 仅差模块名与实体称谓，统一用基类实例化，避免 18 份重复代码
_SERVICES: dict[str, BaseService] = {}


def _service(spec: ModuleSpec) -> BaseService:
    if spec.name not in _SERVICES:
        _SERVICES[spec.name] = BaseService.for_spec(spec)
    return _SERVICES[spec.name]


def _batch_meta() -> dict[str, Any]:
    info = store.release_info() or {}
    return {"batch_id": info.get("batch_id"), "released_at": info.get("released_at")}


def build_router(spec: ModuleSpec) -> APIRouter:
    router = APIRouter(prefix=f"/api/{spec.name}", tags=[spec.label])
    service = _service(spec)
    key_field = spec.required_fields[0]
    max_size = 200

    @router.get("/export")
    def export_entries() -> dict[str, Any]:
        """导出当前模块台账全量数据（盖当前发布批次）。"""
        items, total = service.list_entries(page=1, size=10000)
        return {
            "module": spec.name,
            "label": spec.label,
            "total": total,
            "items": items,
            **_batch_meta(),
        }

    @router.get("")
    def list_entries(
        keyword: str | None = Query(default=None, description=f"按{key_field}检索"),
        status: str | None = Query(default=None, description="按状态过滤"),
        batch_id: str | None = Query(default=None, description="按批次过滤，默认当前发布批次口径"),
        page: int = 1,
        size: int = 20,
    ) -> dict[str, Any]:
        if size > max_size:
            raise HTTPException(status_code=400, detail=f"每页最多 {max_size} 条，请缩小分页范围")
        items, total = service.list_entries(
            keyword=keyword, status=status, batch_id=batch_id, page=page, size=size
        )
        # 分页信封同时是台账批次戳：汇总页/台账/偏离清单据此核对同一批次
        return {
            "items": items,
            "total": total,
            "page": page,
            "size": size,
            "module": spec.name,
            **_batch_meta(),
        }

    @router.get("/{entry_id}", response_model=dict)
    def get_entry(entry_id: int) -> dict[str, Any]:
        entry = service.get_entry(entry_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"{spec.label} {entry_id} 不存在或已归档")
        return entry

    @router.post("", response_model=ActionResult)
    def create_entry(payload: EntryPayload) -> ActionResult:
        entry, missing = service.create_entry(payload.values)
        if missing:
            return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
        return ActionResult(ok=True, message=f"{spec.label}已登记", entry=entry)

    @router.post("/{entry_id}/actions", response_model=ActionResult)
    def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
        action = str(payload.values.get("action") or "").strip()
        entry, message = service.run_action(entry_id, action)
        if entry is None:
            return ActionResult(ok=False, message=message)
        return ActionResult(ok=True, message=message, entry=entry)

    return router
