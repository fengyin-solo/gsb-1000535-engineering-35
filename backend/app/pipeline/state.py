"""流水线持久化层。

布局（DATA_DIR 下）：
    state.json                      当前指针：已发布批次、就绪状态、种子版本
    runs/<batch_id>.json            每次流水线运行的检查点/阶段状态
    ledgers.json                    全量活台账（跨批次）
    summary.json                    汇总页快照（核对结果 + 重算统计）
    deviations.json                 偏离清单
    batches/<batch_id>.json         不可变批次快照（台账/汇总/偏离三件套）
    queue/jobs.json                 文件队列：台账装载作业（支持断点续传）
    cache/agg.json                  文件缓存：汇总统计结果

所有写操作都是 “写临时文件 + fsync + os.replace”，连接断开/进程被杀时
不会出现半截文件；运行级操作整体在 fcntl 文件锁内串行。
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import tempfile
from typing import Any, Iterator

from app.config import settings


def data_dir() -> str:
    """动态读取：测试/entrypoint 切换 DATA_DIR 后无需重新导入模块。"""
    return os.environ.get("DATA_DIR") or settings.data_dir


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def path(name: str) -> str:
    return os.path.join(data_dir(), name)


def atomic_write_json(name: str, payload: Any) -> str:
    """原子写 JSON：同路径重复写同样内容结果一致（幂等）。"""
    target = path(name)
    _ensure_dir(os.path.dirname(target) or data_dir())
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp-", dir=os.path.dirname(target))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)
        raise
    return target


def read_json(name: str, default: Any) -> Any:
    target = path(name)
    try:
        with open(target, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


@contextlib.contextmanager
def run_lock() -> Iterator[None]:
    """串行化流水线提交：重复执行在锁外排队，进入后从检查点继续。"""
    _ensure_dir(data_dir())
    lock_path = path("pipeline.lock")
    with open(lock_path, "a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


STATE_FILE = "state.json"
RUN_FILE_TMPL = "runs/%s.json"
LEDGER_FILE = "ledgers.json"
SUMMARY_FILE = "summary.json"
DEVIATION_FILE = "deviations.json"
BATCH_FILE_TMPL = "batches/%s.json"
QUEUE_FILE = "queue/jobs.json"
CACHE_FILE = "cache/agg.json"

DEFAULT_STATE: dict[str, Any] = {
    "ready": False,
    "current_batch_id": None,
    "seed_version": None,
    "business_date": None,
    "timezone": None,
    "published_at": None,
    "history": [],
}


def load_state() -> dict[str, Any]:
    state = read_json(STATE_FILE, None)
    if not isinstance(state, dict):
        return dict(DEFAULT_STATE)
    merged = dict(DEFAULT_STATE)
    merged.update(state)
    return merged


def save_state(state: dict[str, Any]) -> None:
    atomic_write_json(STATE_FILE, state)


def run_file(batch_id: str) -> str:
    return RUN_FILE_TMPL % batch_id


def batch_file(batch_id: str) -> str:
    return BATCH_FILE_TMPL % batch_id
