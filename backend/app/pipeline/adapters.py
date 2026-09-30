"""缓存与队列适配层。

- file：基于 DATA_DIR 的文件实现，本地开发零外部依赖，作业天然落盘，
  连接断开复位后从检查点继续；
- redis：基于 socket 的 RESP 最小客户端，staging/prod 使用。流水线只用
  PING/GET/SET/LPUSH/RPOP/HSET/HGET/HGETALL，标准库即可实现。

部署前 preflight 必须确认所选后端可用；不可用时阻断发布并枚举依赖。
"""
from __future__ import annotations

import json
import os
import socket
import time
from typing import Any

from app.config import settings
from app.pipeline import state as state_mod


class AdapterUnavailable(RuntimeError):
    """缓存或队列不可用（未连接 / 连接被拒 / 探测失败）。"""


# --------------------------------------------------------------------------- #
# 文件实现
# --------------------------------------------------------------------------- #
class FileAdapter:
    """文件缓存 / 文件队列。cache 与 queue 各一个实例，落盘即提交。"""

    kind = "file"

    def __init__(self, role: str) -> None:
        self.role = role  # cache / queue
        self.available: bool | None = None
        self.last_error: str | None = None

    def describe(self) -> str:
        target = state_mod.QUEUE_FILE if self.role == "queue" else state_mod.CACHE_FILE
        return f"{self.role}:file:{state_mod.path(target)}"

    def ping(self) -> bool:
        try:
            directory = state_mod.path(self.role)
            state_mod._ensure_dir(directory)
            probe = os.path.join(directory, ".probe")
            with open(probe, "w", encoding="utf-8") as handle:
                handle.write("ok")
            self.available = True
            self.last_error = None
            return True
        except OSError as exc:
            self.available = False
            self.last_error = str(exc)
            return False

    # -- 缓存 ---------------------------------------------------------------- #
    def cache_get(self, key: str) -> Any:
        store = state_mod.read_json(state_mod.CACHE_FILE, {})
        return store.get(key)

    def cache_set(self, key: str, value: Any) -> None:
        store = state_mod.read_json(state_mod.CACHE_FILE, {})
        store[key] = value
        state_mod.atomic_write_json(state_mod.CACHE_FILE, store)

    # -- 队列 ---------------------------------------------------------------- #
    def _job_identity(self, job: dict[str, Any]) -> tuple[str, str, int]:
        return job["batch_id"], job["stage"], job["seq"]

    def enqueue(self, job: dict[str, Any]) -> None:
        jobs = state_mod.read_json(state_mod.QUEUE_FILE, [])
        # 幂等：同一 (batch, stage, seq) 已存在则更新载荷，不新增
        for existing in jobs:
            if self._job_identity(existing) == self._job_identity(job):
                existing.update(job)
                state_mod.atomic_write_json(state_mod.QUEUE_FILE, jobs)
                return
        jobs.append(job)
        state_mod.atomic_write_json(state_mod.QUEUE_FILE, jobs)

    def claim(self, batch_id: str) -> dict[str, Any] | None:
        """认领一条本批次待处理作业；全部完成时返回 None。

        running 超过 60 秒视为提交方已断开，复位为 pending 重新认领，
        作业天然幂等，重复提交不会污染数据。
        """
        jobs = state_mod.read_json(state_mod.QUEUE_FILE, [])
        stale_before = time.time() - 60
        for job in jobs:
            if job.get("batch_id") != batch_id:
                continue
            if job.get("status") == "running" and job.get("claimed_at", 0) < stale_before:
                job["status"] = "pending"
                job.pop("claimed_at", None)
            if job.get("status") == "pending":
                job["status"] = "running"
                job["claimed_at"] = time.time()
                state_mod.atomic_write_json(state_mod.QUEUE_FILE, jobs)
                return job
        return None

    def complete(self, job_id: str, result: Any = None) -> None:
        jobs = state_mod.read_json(state_mod.QUEUE_FILE, [])
        for job in jobs:
            if job["job_id"] == job_id:
                job["status"] = "done"
                job["result"] = result
                job["finished_at"] = time.time()
        state_mod.atomic_write_json(state_mod.QUEUE_FILE, jobs)

    def fail(self, job_id: str, error: str) -> None:
        jobs = state_mod.read_json(state_mod.QUEUE_FILE, [])
        for job in jobs:
            if job["job_id"] == job_id:
                # 复位为 pending：连接断开复位后重新认领，从检查点再次提交
                job["status"] = "pending"
                job["last_error"] = error
                job["attempts"] = int(job.get("attempts", 0)) + 1
        state_mod.atomic_write_json(state_mod.QUEUE_FILE, jobs)

    def pending_count(self, batch_id: str) -> int:
        jobs = state_mod.read_json(state_mod.QUEUE_FILE, [])
        return sum(
            1 for job in jobs
            if job.get("batch_id") == batch_id and job.get("status") in {"pending", "running"}
        )


# --------------------------------------------------------------------------- #
# Redis 实现（最小 RESP 客户端）
# --------------------------------------------------------------------------- #
class RedisAdapter:
    kind = "redis"

    def __init__(self, role: str) -> None:
        self.role = role
        self.url = settings.redis_url
        self.host, self.port, self.db, self.password = _parse_redis_url(self.url)
        self.available: bool | None = None
        self.last_error: str | None = None

    def describe(self) -> str:
        masked = self.url.replace(self.password, "***") if self.password else self.url
        return f"{self.role}:redis:{masked}"

    def _connect(self) -> socket.socket:
        try:
            sock = socket.create_connection(
                (self.host, self.port), timeout=settings.dep_timeout_seconds
            )
        except OSError as exc:
            self.available = False
            self.last_error = str(exc)
            raise AdapterUnavailable(f"Redis 连接失败 {self.host}:{self.port}：{exc}") from exc
        sock.settimeout(settings.dep_timeout_seconds)
        if self.password:
            self._command(sock, "AUTH", self.password)
        if self.db:
            self._command(sock, "SELECT", str(self.db))
        return sock

    @staticmethod
    def _encode(*parts: str) -> bytes:
        out = [f"*{len(parts)}\r\n".encode()]
        for part in parts:
            data = str(part).encode("utf-8")
            out.append(b"$" + str(len(data)).encode() + b"\r\n" + data + b"\r\n")
        return b"".join(out)

    def _read(self, sock: socket.socket) -> Any:
        buffer = b""

        def read_more() -> None:
            nonlocal buffer
            chunk = sock.recv(4096)
            if not chunk:
                raise AdapterUnavailable("Redis 连接已断开")
            buffer += chunk

        def read_line() -> bytes:
            nonlocal buffer
            while b"\r\n" not in buffer:
                read_more()
            line, buffer = buffer.split(b"\r\n", 1)
            return line

        line = read_line()
        prefix, payload = line[:1], line[1:]
        if prefix == b"+":
            return payload.decode()
        if prefix == b"-":
            raise AdapterUnavailable(f"Redis 返回错误：{payload.decode(errors='replace')}")
        if prefix == b":":
            return int(payload)
        if prefix == b"$":
            length = int(payload)
            if length == -1:
                return None
            while len(buffer) < length + 2:
                read_more()
            data, buffer = buffer[:length], buffer[length + 2:]
            return data.decode("utf-8")
        if prefix == b"*":
            count = int(payload)
            if count == -1:
                return None
            return [self._read(sock) for _ in range(count)]
        raise AdapterUnavailable(f"Redis 响应无法解析：{line!r}")

    def _command(self, sock: socket.socket, *parts: str) -> Any:
        sock.sendall(self._encode(*parts))
        return self._read(sock)

    def ping(self) -> bool:
        try:
            with self._connect() as sock:
                reply = self._command(sock, "PING")
            self.available = reply == "PONG"
            self.last_error = None
            return self.available
        except AdapterUnavailable as exc:
            self.available = False
            self.last_error = str(exc)
            return False

    # -- 缓存 ---------------------------------------------------------------- #
    def cache_get(self, key: str) -> Any:
        with self._connect() as sock:
            raw = self._command(sock, "GET", f"geo:cache:{key}")
        return json.loads(raw) if raw else None

    def cache_set(self, key: str, value: Any) -> None:
        with self._connect() as sock:
            self._command(sock, "SET", f"geo:cache:{key}", json.dumps(value, ensure_ascii=False))

    # -- 队列：pending 列表 + 作业 hash + 状态 zset（用 list 近似） ----------- #
    def _q(self, name: str) -> str:
        return f"geo:queue:{name}"

    def enqueue(self, job: dict[str, Any]) -> None:
        job_id = job["job_id"]
        identity = f"{job['batch_id']}|{job['stage']}|{job['seq']}"
        with self._connect() as sock:
            # 幂等：身份索引已存在则只刷新载荷
            existing_id = self._command(sock, "HGET", self._q("index"), identity)
            if not existing_id:
                self._command(sock, "HSET", self._q("index"), identity, job_id)
                self._command(sock, "LPUSH", self._q("pending"), job_id)
            self._command(
                sock, "HSET", self._q("jobs"), job_id,
                json.dumps(job, ensure_ascii=False),
            )

    def claim(self, batch_id: str) -> dict[str, Any] | None:
        with self._connect() as sock:
            while True:
                job_id = self._command(sock, "RPOP", self._q("pending"))
                if job_id is None:
                    return None
                raw = self._command(sock, "HGET", self._q("jobs"), job_id)
                job = json.loads(raw)
                if job.get("batch_id") != batch_id:
                    # 不属于本批次，放回队首继续找
                    self._command(sock, "LPUSH", self._q("pending"), job_id)
                    return None
                job["status"] = "running"
                job["claimed_at"] = time.time()
                self._command(
                    sock, "HSET", self._q("jobs"), job_id,
                    json.dumps(job, ensure_ascii=False),
                )
                return job

    def complete(self, job_id: str, result: Any = None) -> None:
        with self._connect() as sock:
            raw = self._command(sock, "HGET", self._q("jobs"), job_id)
            job = json.loads(raw)
            job["status"] = "done"
            job["result"] = result
            job["finished_at"] = time.time()
            self._command(
                sock, "HSET", self._q("jobs"), job_id,
                json.dumps(job, ensure_ascii=False),
            )

    def fail(self, job_id: str, error: str) -> None:
        with self._connect() as sock:
            raw = self._command(sock, "HGET", self._q("jobs"), job_id)
            job = json.loads(raw)
            # 复位重新入队：从检查点再次提交
            job["status"] = "pending"
            job["last_error"] = error
            job["attempts"] = int(job.get("attempts", 0)) + 1
            self._command(
                sock, "HSET", self._q("jobs"), job_id,
                json.dumps(job, ensure_ascii=False),
            )
            self._command(sock, "LPUSH", self._q("pending"), job_id)

    def pending_count(self, batch_id: str) -> int:
        # 简化口径：扫描作业 hash 统计本批次未完成数（RESP 无 HSCAN 复杂结构时仍可跑）
        with self._connect() as sock:
            values = self._command(sock, "HVALS", self._q("jobs")) or []
        count = 0
        for raw in values:
            job = json.loads(raw)
            if job.get("batch_id") == batch_id and job.get("status") in {"pending", "running"}:
                count += 1
        return count


def _parse_redis_url(url: str) -> tuple[str, int, int, str | None]:
    body = url.removeprefix("rediss://").removeprefix("redis://")
    password: str | None = None
    if "@" in body:
        creds, body = body.split("@", 1)
        password = creds.split(":", 1)[1] if ":" in creds else creds
    host_port, _, db_part = body.partition("/")
    if ":" in host_port:
        host, port_raw = host_port.rsplit(":", 1)
        port = int(port_raw)
    else:
        host, port = host_port, 6379
    db = int(db_part) if db_part and db_part.isdigit() else 0
    return host, port, db, password


def _cache_backend_name() -> str:
    explicit = os.environ.get("CACHE_BACKEND")
    if explicit:
        return explicit.strip()
    env = os.environ.get("APP_ENV", "local").strip()
    return "file" if env == "local" else "redis"


def _queue_backend_name() -> str:
    explicit = os.environ.get("QUEUE_BACKEND")
    if explicit:
        return explicit.strip()
    env = os.environ.get("APP_ENV", "local").strip()
    return "file" if env == "local" else "redis"


def get_cache_adapter() -> FileAdapter | RedisAdapter:
    return FileAdapter("cache") if _cache_backend_name() == "file" else RedisAdapter("cache")


def get_queue_adapter() -> FileAdapter | RedisAdapter:
    return FileAdapter("queue") if _queue_backend_name() == "file" else RedisAdapter("queue")
