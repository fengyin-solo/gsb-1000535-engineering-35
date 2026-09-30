"""部署前闸门：缓存 / 队列依赖逐项探测，任何一个不可用都阻断发布。

依赖用形如 ``kind:target`` 的描述登记（环境变量 REQUIRED_CACHE / REQUIRED_QUEUE）：

* ``file:<相对路径>``：本地文件缓存/队列，检查可创建、可写、可读取；
* ``redis://[host:port/db]``：TCP 连通即视为可用（不引入 redis 客户端依赖）；
* ``fail:<名称>``：测试专用，永远不可用，用来验证阻断与枚举行为。

闸门返回结构化结果，调用方据此决定是否继续发布；不存在“静默跳过”。
"""
from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import Any

from app import clock
from app.config import settings


@dataclass(frozen=True)
class Dependency:
    kind: str  # cache / queue
    ref: str

    @property
    def type(self) -> str:
        return self.ref.split(":", 1)[0] if ":" in self.ref else "unknown"


def parse(kind: str, refs: list[str]) -> list[Dependency]:
    return [Dependency(kind=kind, ref=ref.strip()) for ref in refs if ref.strip()]


def required_dependencies() -> list[Dependency]:
    return parse("cache", settings.required_cache) + parse("queue", settings.required_queue)


def _check_file(ref: str) -> tuple[bool, str]:
    from pathlib import Path

    target = ref[len("file:"):] or "var/cache"
    path = Path(target)
    if not path.is_absolute():
        # 相对路径锚定到流水线状态目录，保证部署探针落到同一持久卷
        path = settings.state_dir / path
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".probe"
        probe.write_text("probe", encoding="utf-8")
        probe.read_text(encoding="utf-8")
        return True, f"本地文件依赖可读写：{path}"
    except OSError as exc:
        return False, f"本地文件依赖不可用：{exc}"


def _check_tcp(ref: str, *, timeout: float = 2.0) -> tuple[bool, str]:
    body = ref.split("://", 1)[-1].split("/", 1)[0]
    host, _, port_text = body.partition(":")
    port = int(port_text or "6379")
    try:
        with socket.create_connection((host or "127.0.0.1", port), timeout=timeout):
            return True, f"TCP 连接成功：{host}:{port}"
    except OSError as exc:
        return False, f"TCP 连接失败：{host}:{port}（{exc}）"


def check_one(dep: Dependency) -> dict[str, Any]:
    if dep.ref.startswith("file:"):
        ok, message = _check_file(dep.ref)
    elif dep.ref.startswith(("redis://", "amqp://", "tcp://")):
        ok, message = _check_tcp(dep.ref)
    elif dep.ref.startswith("fail:"):
        ok, message = False, f"被显式标记为不可用：{dep.ref[5:]}"
    else:
        ok, message = False, f"未知依赖类型，无法探测：{dep.ref}"
    return {"kind": dep.kind, "ref": dep.ref, "type": dep.type, "available": ok, "message": message}


def run_gate() -> dict[str, Any]:
    """逐项探测全部登记依赖；任一不可用整体即为 blocked。"""
    checks = [check_one(dep) for dep in required_dependencies()]
    unavailable = [item for item in checks if not item["available"]]
    blocked = bool(unavailable)
    return {
        "checked_at": clock.iso_ts(),
        "env": settings.env,
        "blocked": blocked,
        "required": [
            {"kind": item["kind"], "ref": item["ref"]} for item in checks
        ],
        "checks": checks,
        "unavailable": [
            {"kind": item["kind"], "ref": item["ref"], "message": item["message"]}
            for item in unavailable
        ],
    }
