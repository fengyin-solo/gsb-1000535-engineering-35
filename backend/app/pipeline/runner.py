"""可重复流水线编排器：初始化/构建/部署一条命令跑通，且必须满足：

* 幂等：同一业务批次重跑，结果完全一致，不重复插数据、不产生第二个发布；
* 检查点：连接断开/进程复位后再次执行，从 checkpoint 的下一阶段继续提交；
* 未成功不就绪：任何阶段失败，批次状态停在 failed，release 指针不动；
* 可回滚：按版本（批次号）原子切回历史发布；
* 种子迁移：迁移版本登记一次，既有示例数据按原批次保留。

CLI 见 ``python -m app.pipeline.runner --help``。
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from datetime import date as date_cls
from typing import Any

from app import clock
from app.config import settings
from app.modules import MODULES, REGISTRY
from app.pipeline import backfill, build as build_stage, deps
from app.pipeline.migrations import MIGRATIONS, pending as pending_migrations
from app.pipeline.reconcile import reconcile, verify_artifact
from app.pipeline.state import READY_STAGE, STAGES, PipelineState
from app.seed import SEED_ROWS


class PipelineError(RuntimeError):
    """阶段业务失败（区别于需要重试的瞬时连接错误）。"""


class TransientError(RuntimeError):
    """瞬时错误（如连接断开）：按重试策略退避后可再次提交同一阶段。"""


class Pipeline:
    def __init__(self, state: PipelineState | None = None, *, business_day: date_cls | None = None,
                 build: bool = True, require_manifest: bool = False) -> None:
        self.state = (state or PipelineState()).ensure()
        self.business_day = business_day or clock.today()
        self.batch_id = clock.batch_id_for(self.business_day)
        self.run_build = build
        # 运行镜像不构建前端，但仍要求部署目录里带当前批次构建清单
        self.require_manifest = require_manifest
        # 本批次的固定起始时间戳：重放阶段沿用它，保证快照产物字节一致
        existing = self.state.load_run(self.batch_id)
        self.started_at = str(existing.get("started_at") or clock.iso_ts())
        if not existing.get("started_at"):
            existing["started_at"] = self.started_at
            self.state.save_run(existing)

    # ---- 工作集：永远从内置种子重放迁移得到，确定性可重放 ----
    def _working_tables(self) -> dict[str, list[dict[str, Any]]]:
        tables: dict[str, list[dict[str, Any]]] = {
            name: copy.deepcopy(rows) for name, rows in SEED_ROWS.items()
        }
        applied = {item["id"] for item in self.state.applied_migrations()}
        for migration in MIGRATIONS:  # 全量重放，保证确定性
            tables = migration.apply(tables, self.batch_id)
            if migration.id not in applied:
                self.state.mark_migration_applied(migration.id, {
                    "applied_at": clock.iso_ts(),
                    "run_batch": self.batch_id,
                    "description": migration.description,
                })
        return tables

    def _load_committed_tables(self) -> dict[str, list[dict[str, Any]]] | None:
        snapshot = self.state.load_snapshot(self.batch_id)
        return copy.deepcopy(snapshot["tables"]) if snapshot else None

    # ---- 阶段实现 ----
    def stage_migrate(self, detail: dict[str, Any]) -> dict[str, Any]:
        applied_before = {item["id"] for item in self.state.applied_migrations()}
        tables = self._working_tables()
        newly = [m.id for m in pending_migrations(applied_before)]
        detail["newly_applied"] = newly
        detail["total_tables"] = len(tables)
        detail["total_rows"] = sum(len(rows) for rows in tables.values())
        return {"tables": tables}

    def stage_backfill(self, detail: dict[str, Any], tables: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
        enriched, created = backfill.enrich_backfill(tables, self.batch_id, self.business_day)
        detail["created_rows"] = len(created)
        detail["sample"] = created[:3]
        return {"tables": enriched}

    def stage_reconcile(self, detail: dict[str, Any], tables: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
        artifact = reconcile(tables, self.batch_id, self.business_day, at=self.started_at)
        detail["matched"] = artifact["reconciliation"]["matched"]
        detail["deviations"] = artifact["reconciliation"]["deviation_count"]
        if not artifact["reconciliation"]["matched"]:
            raise PipelineError("对账未通过：汇总页/模块台账/偏离清单批次不一致")
        return {"artifact": artifact}

    def stage_verify(self, detail: dict[str, Any], artifact: dict[str, Any]) -> dict[str, Any]:
        problems = verify_artifact(artifact, self.batch_id)
        detail["problems"] = problems
        if problems:
            raise PipelineError("发布前复核失败：" + "；".join(problems))
        return {}

    def stage_build(self, detail: dict[str, Any]) -> dict[str, Any]:
        if not self.run_build:
            detail["skipped"] = True
            return {"manifest": None}
        result = build_stage.run_build(self.batch_id)
        detail.update(result)
        return {"manifest": result}

    def stage_gate(self, detail: dict[str, Any], manifest: dict[str, Any] | None) -> dict[str, Any]:
        gate = deps.run_gate()
        detail["blocked"] = gate["blocked"]
        detail["unavailable"] = gate["unavailable"]
        if gate["blocked"]:
            raise PipelineError("部署闸门阻断：" + "；".join(
                f"[{item['kind']}] {item['ref']}：{item['message']}" for item in gate["unavailable"]
            ))
        if self.run_build and not manifest:
            raise PipelineError("部署闸门阻断：缺少当前批次构建清单")
        if self.require_manifest and not build_stage.manifest_for_current_batch(self.batch_id):
            raise PipelineError("部署闸门阻断：部署产物里缺少当前批次构建清单，疑似旧版本产物")
        return {"gate": gate}

    def stage_release(
        self, detail: dict[str, Any], artifact: dict[str, Any], tables: dict[str, list[dict[str, Any]]]
    ) -> dict[str, Any]:
        # 1) 不可变快照落盘（同批次内容必须一致，否则拒绝）
        snapshot = {
            "version": 1,
            "batch_id": self.batch_id,
            "business_date": clock.iso(self.business_day),
            "timezone": settings.business_tz,
            "app_version": settings.app_version,
            "created_at": self.started_at,
            "tables": tables,
            "artifact": artifact,
        }
        self.state.save_snapshot(self.batch_id, snapshot)

        # 2) 幂等：发布指针已指向本批次时直接返回，不重复登记
        current = self.state.load_release()
        if current and current.get("batch_id") == self.batch_id:
            detail["idempotent"] = True
            return {"release": current}

        # 3) 原子切换发布指针，并把上一版记入历史用于回滚
        previous = current
        release = {
            "batch_id": self.batch_id,
            "app_version": settings.app_version,
            "business_date": clock.iso(self.business_day),
            "released_at": clock.iso_ts(),
            "snapshot": str(self.state.snapshot_path(self.batch_id)),
            "previous_batch": previous.get("batch_id") if previous else None,
        }
        self.state.append_release_history(release)
        self.state.save_release(release)
        detail["previous_batch"] = release["previous_batch"]
        return {"release": release}

    # ---- 编排 ----
    def run(self, *, simulate_failure: str | None = None) -> dict[str, Any]:
        run = self.state.load_run(self.batch_id)
        if run.get("checkpoint") == READY_STAGE and self.state.load_release():
            # 已成功发布：回填可能被独立构建清掉的 dist 清单，再只读确认，
            # 不重放写阶段，保证幂等。
            build_stage.restore_dist_manifest(self.batch_id)
            return self._idempotent_confirm(run)

        context: dict[str, Any] = {"tables": None, "artifact": None, "manifest": None}
        for stage in STAGES:
            if not self.run_build and stage == "build":
                # 运行侧不负责产物构建，但部署闸门（依赖+构建清单）照常执行
                continue
            if self.state.stage_status(self.batch_id, stage) == "succeeded":
                # 已成功阶段：从既有快照/运行记录恢复上下文后跳过，绝不重放写动作
                self._restore_context(stage, context)
                if stage == "build":
                    if context.get("manifest") is None:
                        context["manifest"] = build_stage.load_manifest(self.batch_id) or {
                            "batch_id": self.batch_id, "restored": True,
                        }
                    # dist 可能被独立构建清空过：构建阶段已成功时，用状态目录里的
                    # 清单副本回填 dist，保证部署闸门与镜像始终拿到当前批次清单。
                    build_stage.restore_dist_manifest(self.batch_id)
                continue
            run = self._execute_stage(stage, context, simulate_failure)
            if run.get("status") == "failed":
                return run
        return self.state.load_run(self.batch_id)

    def _idempotent_confirm(self, run: dict[str, Any]) -> dict[str, Any]:
        release = self.state.load_release()
        snapshot = self.state.load_snapshot(self.batch_id)
        if not release or release.get("batch_id") != self.batch_id or snapshot is None:
            # ready 但指针/快照缺失属于损坏状态：回到 release 前重新提交
            run["status"] = "running"
            self.state.save_run(run)
            return self.run()
        return run

    def _restore_context(self, stage: str, context: dict[str, Any]) -> None:
        snapshot = self.state.load_snapshot(self.batch_id)
        if snapshot:
            context["tables"] = copy.deepcopy(snapshot["tables"])
            context["artifact"] = copy.deepcopy(snapshot["artifact"])
            return snapshot
        if stage in {"backfill", "reconcile", "verify", "release"}:
            tables = self._working_tables()
            tables, _ = backfill.enrich_backfill(tables, self.batch_id, self.business_day)
            context["tables"] = tables
            context["artifact"] = reconcile(tables, self.batch_id, self.business_day, at=self.started_at)
        if stage == "gate":
            context["manifest"] = build_stage.load_manifest(self.batch_id)
        return None

    def _execute_stage(
        self, stage: str, context: dict[str, Any], simulate_failure: str | None
    ) -> dict[str, Any]:
        started = clock.iso_ts()
        detail: dict[str, Any] = {}
        attempts = max(settings.retry_attempts, 1)
        last_error = ""
        for attempt in range(1, attempts + 1):
            try:
                output = self._dispatch(stage, context, detail, simulate_failure)
                context.update({k: v for k, v in output.items() if v is not None})
                return self.state.mark_stage(
                    self.batch_id, stage, status="succeeded",
                    detail=detail, started_at=started, finished_at=clock.iso_ts(),
                )
            except PipelineError as exc:  # 业务失败不重试
                last_error = str(exc)
                detail["error"] = last_error
                break
            except Exception as exc:  # 连接断开等瞬时故障：退避重试同一阶段
                last_error = f"{type(exc).__name__}: {exc}"
                detail["last_error"] = last_error
                if attempt < attempts:
                    time.sleep(settings.retry_backoff_seconds * attempt)
                    continue
                detail["error"] = f"重试 {attempts} 次仍失败：{last_error}"
                break
        return self.state.mark_stage(
            self.batch_id, stage, status="failed",
            detail=detail, started_at=started, finished_at=clock.iso_ts(),
        )

    def _dispatch(
        self, stage: str, context: dict[str, Any], detail: dict[str, Any],
        simulate_failure: str | None,
    ) -> dict[str, Any]:
        if simulate_failure == stage:
            # 连接断开复位演练：先抛瞬时错误，流水线必须能从检查点继续
            raise ConnectionError(f"模拟 {stage} 阶段连接断开")
        if stage == "migrate":
            return self.stage_migrate(detail)
        if stage == "backfill":
            return self.stage_backfill(detail, context["tables"])
        if stage == "reconcile":
            return self.stage_reconcile(detail, context["tables"])
        if stage == "verify":
            return self.stage_verify(detail, context["artifact"])
        if stage == "build":
            return self.stage_build(detail)
        if stage == "gate":
            return self.stage_gate(detail, context.get("manifest"))
        if stage == "release":
            return self.stage_release(detail, context["artifact"], context["tables"])
        raise PipelineError(f"未知阶段：{stage}")

    # ---- 回滚 ----
    def rollback(self, target_batch: str) -> dict[str, Any]:
        """按版本（批次号）回滚：校验目标快照存在且自身 ready，再原子切指针。"""
        target_snapshot = self.state.load_snapshot(target_batch)
        if target_snapshot is None:
            raise PipelineError(f"回滚目标批次 {target_batch} 不存在，无法回滚")
        target_run = self.state.load_run(target_batch)
        if target_run.get("status") != "ready":
            raise PipelineError(f"回滚目标批次 {target_batch} 状态为 {target_run.get('status')}，不是成功发布")
        current = self.state.load_release()
        rollback_record = {
            "batch_id": target_batch,
            "app_version": target_snapshot.get("app_version"),
            "business_date": target_snapshot.get("business_date"),
            "released_at": clock.iso_ts(),
            "snapshot": str(self.state.snapshot_path(target_batch)),
            "rolled_back_from": current.get("batch_id") if current else None,
            "rollback": True,
        }
        self.state.append_release_history(rollback_record)
        self.state.save_release(rollback_record)
        return rollback_record

    # ---- 状态查询 ----
    def status(self) -> dict[str, Any]:
        run = self.state.load_run(self.batch_id)
        return {
            "batch_id": self.batch_id,
            "business_date": clock.iso(self.business_day),
            "status": run.get("status"),
            "checkpoint": run.get("checkpoint"),
            "ready": run.get("status") == "ready",
            "stages": run.get("stages", {}),
            "release": self.state.load_release(),
            "migrations": self.state.applied_migrations(),
        }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="勘探数据流水线：初始化、构建、部署一条可重复流水线")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="执行/续跑当前业务批次流水线")
    run_p.add_argument("--as-of", help="固定业务日期（YYYY-MM-DD），默认业务时区今天")
    run_p.add_argument("--no-build", action="store_true", help="跳过前端构建（仅初始化与对账）")
    run_p.add_argument("--require-manifest", action="store_true",
                       help="运行侧不构建，但闸门必须找到当前批次构建清单（部署镜像用）")
    run_p.add_argument("--fail-stage", choices=STAGES, help="演练：指定阶段注入连接断开")

    sub.add_parser("status", help="查看当前批次检查点与发布状态")

    rb = sub.add_parser("rollback", help="按版本（批次号）回滚发布")
    rb.add_argument("batch", help="目标批次号，如 batch-2026-09-29")

    sub.add_parser("gate", help="单独执行部署前依赖闸门探测")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    business_day = clock.today()
    if getattr(args, "as_of", None):
        business_day = date_cls.fromisoformat(args.as_of)
    pipeline = Pipeline(
        business_day=business_day,
        build=not getattr(args, "no_build", False),
        require_manifest=getattr(args, "require_manifest", False),
    )

    if args.command == "run":
        result = pipeline.run(simulate_failure=getattr(args, "fail_stage", None))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("status") == "ready" else 2
    if args.command == "status":
        print(json.dumps(pipeline.status(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "rollback":
        record = pipeline.rollback(args.batch)
        print(json.dumps(record, ensure_ascii=False, indent=2))
        return 0
    if args.command == "gate":
        gate = deps.run_gate()
        print(json.dumps(gate, ensure_ascii=False, indent=2))
        return 0 if not gate["blocked"] else 3
    return 1


if __name__ == "__main__":
    sys.exit(main())
