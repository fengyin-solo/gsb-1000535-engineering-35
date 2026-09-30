"""业务服务通用基类：状态流转、字段校验与筛选口径对所有模块一致。

各模块 service 只保留模块标识与中文实体称谓，规则全部来自 modules.REGISTRY，
这样流水线新增的批次字段（batch_id / recorded_at 等）在任何模块都走同一套读写。
"""
from __future__ import annotations

from typing import Any

from app.modules import ModuleSpec, get as get_spec
from app.store import store


class BaseService:
    module: str = ""
    entity: str = "记录"  # 错误提示里的中文称谓，由子类覆盖

    def __init__(self) -> None:
        self.spec: ModuleSpec = get_spec(self.module)

    @classmethod
    def for_spec(cls, spec: ModuleSpec) -> "BaseService":
        service = cls.__new__(cls)
        service.spec = spec
        service.module = spec.name
        service.entity = spec.label
        return service

    # ---- 读取 ----
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        batch_id: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(self.module)
        key_field = self.spec.required_fields[0]
        if keyword:
            rows = [row for row in rows if keyword in str(row.get(key_field, ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if batch_id:
            rows = [row for row in rows if str(row.get("batch_id")) == batch_id]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(self.module, entry_id)

    # ---- 写入 ----
    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in self.spec.required_fields if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(self.module)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in self.spec.required_fields})
        entry["status"] = self.spec.statuses[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(self.module, entry_id)
        if entry is None:
            return None, f"{self.entity} {entry_id} 不存在或已归档"
        if action not in self.spec.actions:
            return None, f"动作「{action}」不属于{self.spec.label}可执行范围"
        target = self.spec.target_of(action)
        if target is None or target not in self.spec.statuses:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != self.spec.statuses[-1]
        entry["abnormal"] = False
        return entry, f"{self.entity}已{action}"
