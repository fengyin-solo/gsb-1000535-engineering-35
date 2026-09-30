"""部署前依赖探测：缓存 / 队列不可用时阻断发布，并枚举缺失依赖。

本地（APP_ENV=local）默认且推荐 file 后端；staging/prod 默认 redis，
显式把 CACHE_BACKEND / QUEUE_BACKEND 配成 redis 时同样必须通过探测。
任一依赖在 DEP_ATTEMPTS 次重试内仍不可用，preflight 阶段失败，
后续阶段与发布一律不得执行。
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

from app.config import settings
from app.pipeline.adapters import (
    AdapterUnavailable,
    get_cache_adapter,
    get_queue_adapter,
)


@dataclass
class DependencyReport:
    ok: bool
    dependencies: list[dict[str, object]] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {"ok": self.ok, "dependencies": self.dependencies,
                "env": settings.env, "business_timezone": settings.business_timezone}

    def blocked_names(self) -> list[str]:
        return [str(dep["name"]) for dep in self.dependencies if not bool(dep["available"])]

    def describe_blockers(self) -> list[str]:
        return [
            f"{dep['name']} 不可用：{dep['detail']}（{dep['endpoint']}）"
            for dep in self.dependencies
            if not bool(dep["available"])
        ]


def _probe(name: str, adapter: object, required: bool, role: str) -> dict[str, object]:
    last_error = "未探测"
    backend = (os.environ.get(f"{role.upper()}_BACKEND", "").strip()
               or ("file" if settings.env == "local" else "redis"))
    for attempt in range(1, settings.dep_attempts + 1):
        try:
            if bool(adapter.ping()):
                return {
                    "name": name,
                    "role": role,
                    "backend": backend,
                    "endpoint": adapter.describe(),
                    "required": required,
                    "available": True,
                    "attempts": attempt,
                    "detail": "探测通过",
                }
            last_error = str(getattr(adapter, "last_error", None) or "ping 未通过")
        except AdapterUnavailable as exc:  # 连接被重置等
            last_error = str(exc)
        if attempt < settings.dep_attempts:
            time.sleep(0.2 * attempt)  # 连接断开复位后重试
    return {
        "name": name,
        "role": role,
        "backend": backend,
        "endpoint": adapter.describe(),
        "required": required,
        "available": False,
        "attempts": settings.dep_attempts,
        "detail": last_error,
    }


def run_preflight() -> DependencyReport:
    """枚举全部部署依赖并逐个探测；缓存/队列是阻断项。"""
    cache = get_cache_adapter()
    queue = get_queue_adapter()
    dependencies = [
        _probe("缓存（汇总统计）", cache, required=True, role="cache"),
        _probe("队列（台账装载作业）", queue, required=True, role="queue"),
    ]
    blocked = [dep for dep in dependencies if not bool(dep["available"])]
    return DependencyReport(ok=not blocked, dependencies=dependencies)
