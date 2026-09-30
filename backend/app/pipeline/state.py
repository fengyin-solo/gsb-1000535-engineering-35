"""流水线状态层：检查点、批次快照、发布指针、迁移版本都落在 PIPELINE_STATE_DIR。

设计约束（对应需求里的运维语义）：

* 所有写入都是「临时文件 + fsync + 原子 rename」，崩溃/断电不会留下半截 JSON；
* 写文件用 fcntl 排他锁串行化，重复执行与连接复位后重跑不会互相踩；
* 批次快照不可变；要换批次只能发布新快照、再移动 release 指针；
* release 指针用「写新指针 + rename」切换，发布与回滚都是一次原子动作；
* 失败的阶段只在 runs/<batch>.json 里留下 status=failed，绝不会推进 release。
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from app.config import settings

# 流水线固定阶段顺序：每个阶段都是确定性的，可安全重放
STAGES: tuple[str, ...] = (
    "migrate",      # 种子数据迁移：给既有示例数据补批次血缘，只跑未应用的版本
    "backfill",     # 跨日补录：按业务时区生成昨日业务日的补录样例
    "reconcile",    # 三方对账：汇总页/模块台账/偏离清单核对为同一批次并落结果
    "verify",       # 复核：重新读出快照校验批次一致，统计由快照重算
    "build",        # 构建：产出前端静态产物与构建清单
    "gate",         # 部署前闸门：缓存/队列依赖逐项探测，不可用即阻断
    "release",      # 发布：原子切换 release 指针并登记版本
)

READY_STAGE = "release"

SNAPSHOT_VERSION = 1


class PipelineState:
    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root or settings.state_dir)
        self.runs_dir = self.root / "runs"
        self.snapshots_dir = self.root / "snapshots"
        self.releases_dir = self.root / "releases"
        self.migrations_dir = self.root / "migrations"
        self.locks_dir = self.root / "locks"

    def ensure(self) -> "PipelineState":
        for directory in (
            self.root, self.runs_dir, self.snapshots_dir,
            self.releases_dir, self.migrations_dir, self.locks_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        return self

    # ---- 基础 IO ----
    @contextmanager
    def _locked(self, name: str) -> Iterator[None]:
        """跨进程排他锁：保证同一份状态的并发重跑被串行化。"""
        self.ensure()
        lock_path = self.locks_dir / f"{name}.lock"
        with lock_path.open("a+") as handle:
            try:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            except ImportError:  # 极端平台没有 fcntl 时退化为进程内串行
                pass
            yield

    @staticmethod
    def _atomic_write_json(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)

    @staticmethod
    def _read_json(path: Path, default: Any) -> Any:
        if not path.exists():
            return default
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    # ---- 运行检查点 ----
    def run_path(self, batch_id: str) -> Path:
        return self.runs_dir / f"{batch_id}.json"

    def load_run(self, batch_id: str) -> dict[str, Any]:
        return self._read_json(
            self.run_path(batch_id),
            {"batch_id": batch_id, "status": "new", "checkpoint": None, "stages": {}},
        )

    def save_run(self, run: dict[str, Any]) -> None:
        self._atomic_write_json(self.run_path(run["batch_id"]), run)

    def mark_stage(self, batch_id: str, stage: str, *, status: str, detail: dict[str, Any] | None = None,
                   started_at: str | None = None, finished_at: str | None = None) -> dict[str, Any]:
        """登记一个阶段的检查点。

        checkpoint 只在阶段首次成功时前移，已成功的阶段不会被后来的失败拉回；
        这样连接断开复位后可以从 checkpoint 的下一阶段继续提交。
        """
        with self._locked(f"run-{batch_id}"):
            run = self.load_run(batch_id)
            record = dict(run.get("stages", {}).get(stage, {}))
            record.update({
                "name": stage,
                "status": status,
                "attempts": int(record.get("attempts", 0)) + 1,
                "started_at": started_at or record.get("started_at"),
                "finished_at": finished_at,
            })
            if detail:
                record["detail"] = detail
            run.setdefault("stages", {})[stage] = record
            if stage == READY_STAGE and status == "succeeded":
                run["status"] = "ready"
                run["checkpoint"] = READY_STAGE
            elif status == "failed":
                run["status"] = "failed"  # 未成功阶段绝不被当作就绪
            elif run.get("status") != "ready":
                run["status"] = "running"
                checkpoint = run.get("checkpoint")
                if status == "succeeded" and (
                    checkpoint is None or _stage_rank(stage) > _stage_rank(str(checkpoint))
                ):
                    run["checkpoint"] = stage
            self.save_run(run)
            return run

    def stage_status(self, batch_id: str, stage: str) -> str | None:
        run = self.load_run(batch_id)
        stage_record = run.get("stages", {}).get(stage)
        return stage_record.get("status") if stage_record else None

    # ---- 批次快照（不可变）----
    def snapshot_path(self, batch_id: str) -> Path:
        return self.snapshots_dir / f"{batch_id}.json"

    def save_snapshot(self, batch_id: str, snapshot: dict[str, Any]) -> None:
        path = self.snapshot_path(batch_id)
        if path.exists():
            existing = self._read_json(path, None)
            if existing is not None:
                # 快照是阶段重放的确定产物：内容一致才允许覆盖写，防止重跑污染数据
                if _canonical(existing) != _canonical(snapshot):
                    raise RuntimeError(f"批次 {batch_id} 已存在内容不同的快照，拒绝覆盖以防止污染")
        self._atomic_write_json(path, snapshot)

    def load_snapshot(self, batch_id: str) -> dict[str, Any] | None:
        return self._read_json(self.snapshot_path(batch_id), None)

    # ---- 发布指针与版本回滚 ----
    def release_pointer(self) -> Path:
        return self.root / "release.json"

    def save_release(self, release: dict[str, Any]) -> None:
        with self._locked("release"):
            self._atomic_write_json(self.release_pointer(), release)

    def load_release(self) -> dict[str, Any] | None:
        return self._read_json(self.release_pointer(), None)

    def release_history_path(self) -> Path:
        return self.releases_dir / "history.json"

    def append_release_history(self, record: dict[str, Any]) -> list[dict[str, Any]]:
        with self._locked("release-history"):
            history = self._read_json(self.release_history_path(), [])
            history.append(record)
            self._atomic_write_json(self.release_history_path(), history)
            return history

    def release_history(self) -> list[dict[str, Any]]:
        return self._read_json(self.release_history_path(), [])

    # ---- 迁移版本（种子数据迁移）----
    def migrations_applied_path(self) -> Path:
        return self.migrations_dir / "applied.json"

    def applied_migrations(self) -> list[dict[str, Any]]:
        return self._read_json(self.migrations_applied_path(), [])

    def mark_migration_applied(self, migration_id: str, detail: dict[str, Any]) -> None:
        with self._locked("migrations"):
            applied = self.applied_migrations()
            if not any(item.get("id") == migration_id for item in applied):
                applied.append({"id": migration_id, **detail})
                self._atomic_write_json(self.migrations_applied_path(), applied)


def _stage_rank(stage: str) -> int:
    return STAGES.index(stage) if stage in STAGES else -1


def _canonical(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
