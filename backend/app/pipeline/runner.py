"""流水线编排：阶段检查点、断点续跑、就绪门禁、版本回滚。

一次运行的固定阶段：
    preflight -> migrate -> generate -> load -> reconcile -> verify -> publish

语义保证：
- 幂等：同样的 (种子版本, 业务日期, 业务时区) 得到同一批次，重复执行
  只把未成功阶段再跑一遍，已成功阶段从检查点跳过，不产生重复数据；
- 断点续传：任何阶段失败 / 连接断开复位后，再次执行从最后检查点继续，
  未成功阶段绝不当作就绪，ready 只在 publish 成功后置位；
- 可回滚：批次快照不可变，回滚即把当前指针切到历史批次或历史种子版本。
"""
from __future__ import annotations

import copy
import os
from dataclasses import dataclass
from typing import Any

from app.config import settings
from app.pipeline import state as state_mod
from app.pipeline.checksum import digest_payload, table_checksum
from app.pipeline.constants import MODULE_ORDER, STAGES
from app.pipeline.loading import run_load_jobs, seed_load_jobs
from app.pipeline.preflight import run_preflight
from app.pipeline.reconcile import run_reconcile
from app.pipeline.samples import generate_samples
from app.pipeline.seeds import SEED_VERSIONS, seed_registry
from app.pipeline.time_utils import now_local, iso


class PipelineError(RuntimeError):
    """流水线阶段失败（依赖缺失 / 核对不一致 / 作业未完成）。"""


class BlockedByDependency(PipelineError):
    """preflight 未通过，发布被阻断。"""


def _failpoint(stage: str) -> None:
    """测试钩子：FAIL_STAGE=<阶段> 时在该阶段提交前抛错，模拟未成功阶段。"""
    wanted = os.environ.get("FAIL_STAGE", "").strip()
    if wanted and wanted == stage:
        raise PipelineError(f"注入失败：阶段 {stage} 未成功（FAIL_STAGE）")


@dataclass
class StageResult:
    stage: str
    status: str          # skipped / done / failed
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"stage": self.stage, "status": self.status, "detail": self.detail}


class PipelineRunner:
    def __init__(self, *, business_date: str, seed_version: str | None = None,
                 timezone_name: str | None = None) -> None:
        self.business_date = business_date
        self.seed_version = seed_version or settings.seed_version
        if self.seed_version not in SEED_VERSIONS:
            raise PipelineError(f"种子版本 {self.seed_version} 不存在：{SEED_VERSIONS}")
        self.timezone_name = timezone_name or settings.business_timezone
        # 批次 ID 由入参派生，重跑同一批次
        samples = generate_samples(self.seed_version, self.business_date, self.timezone_name)
        self.batch_id = samples[0]
        self.generated_tables = samples[1]
        self.generated_deviations = samples[2]
        self.run_path = state_mod.run_file(self.batch_id)

    # ------------------------------------------------------------------ #
    # 运行记录与检查点
    # ------------------------------------------------------------------ #
    def _new_run(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "seed_version": self.seed_version,
            "business_date": self.business_date,
            "timezone": self.timezone_name,
            "started_at": iso(now_local()),
            "updated_at": iso(now_local()),
            "ready": False,
            "stages": {stage: {"status": "pending", "detail": "", "at": None}
                       for stage in STAGES},
            "preflight": None,
        }

    def _load_run(self) -> dict[str, Any]:
        record = state_mod.read_json(self.run_path, None)
        if not isinstance(record, dict):
            return self._new_run()
        for stage in STAGES:
            record.setdefault("stages", {}).setdefault(
                stage, {"status": "pending", "detail": "", "at": None}
            )
        record.setdefault("ready", False)
        return record

    def _save_run(self, record: dict[str, Any], *, ready: bool | None = None) -> None:
        record["updated_at"] = iso(now_local())
        if ready is not None:
            record["ready"] = ready
        state_mod.atomic_write_json(self.run_path, record)

    def _mark(self, record: dict[str, Any], stage: str, status: str, detail: str) -> None:
        record["stages"][stage] = {"status": status, "detail": detail, "at": iso(now_local())}
        self._save_run(record)

    # ------------------------------------------------------------------ #
    # 入口
    # ------------------------------------------------------------------ #
    def run(self, *, force: bool = False) -> dict[str, Any]:
        """执行（或继续）一次流水线运行。返回最终运行记录。"""
        with state_mod.run_lock():
            record = self._load_run()
            if record.get("ready") and not force:
                return record
            results: list[StageResult] = []
            for stage in STAGES:
                checkpoint = record["stages"][stage]
                if checkpoint["status"] == "done" and not force:
                    results.append(StageResult(stage, "skipped", checkpoint["detail"]))
                    continue
                _failpoint(stage)
                detail = self._execute(stage, record)
                self._mark(record, stage, "done", detail)
                results.append(StageResult(stage, "done", detail))
            # publish 成功才能置就绪
            self._publish_pointer(record)
            self._mark(record, "publish", "done", record["stages"]["publish"]["detail"])
            self._save_run(record, ready=True)
            record["_results"] = [r.as_dict() for r in results]
            return record

    # ------------------------------------------------------------------ #
    # 各阶段
    # ------------------------------------------------------------------ #
    def _execute(self, stage: str, record: dict[str, Any]) -> str:
        if stage == "preflight":
            return self._stage_preflight(record)
        if stage == "migrate":
            return self._stage_migrate(record)
        if stage == "generate":
            return self._stage_generate(record)
        if stage == "load":
            return self._stage_load(record)
        if stage == "reconcile":
            return self._stage_reconcile(record)
        if stage == "verify":
            return self._stage_verify(record)
        if stage == "publish":
            return "等待指针发布"
        raise PipelineError(f"未知阶段：{stage}")

    def _stage_preflight(self, record: dict[str, Any]) -> str:
        report = run_preflight()
        record["preflight"] = report.as_dict()
        if not report.ok:
            blockers = report.describe_blockers()
            self._mark(record, "preflight", "failed", "；".join(blockers))
            raise BlockedByDependency(
                "发布被阻断，以下依赖不可用：\n  - " + "\n  - ".join(report.describe_blockers())
            )
        return "依赖探测通过：" + "、".join(str(d["name"]) for d in report.dependencies)

    def _stage_migrate(self, record: dict[str, Any]) -> str:
        # 种子迁移是 (版本, 业务时区) 的纯函数：确定性重放，原批次（seed-v1）行原样保留
        tables = seed_registry.version(self.seed_version, self.timezone_name)
        checksum = seed_registry.checksum(self.seed_version, self.timezone_name)
        record["seed"] = {"version": self.seed_version, "checksum": checksum,
                          "modules": len(tables),
                          "rows": sum(len(rows) for rows in tables.values())}
        state_mod.atomic_write_json("seeds/current.json",
                                    {"version": self.seed_version, "checksum": checksum,
                                     "tables": tables})
        return f"种子迁移至 {self.seed_version}（{record['seed']['rows']} 行，checksum={checksum[:12]}）"

    def _stage_generate(self, record: dict[str, Any]) -> str:
        total = sum(len(rows) for rows in self.generated_tables.values())
        record["batch"] = {
            "batch_id": self.batch_id,
            "business_date": self.business_date,
            "timezone": self.timezone_name,
            "sample_rows": total,
            "late_entries": len(self.generated_deviations),
        }
        return f"按 {self.timezone_name} 生成业务日 {self.business_date} 样例 {total} 行，跨日补录 {len(self.generated_deviations)} 条"

    def _stage_load(self, record: dict[str, Any]) -> str:
        seed_doc = state_mod.read_json("seeds/current.json", {})
        seed_tables = seed_doc.get("tables", {})
        if seed_doc.get("version") != self.seed_version or not seed_tables:
            raise PipelineError("种子迁移产物缺失或版本不符，请从 migrate 阶段重跑")
        seed_load_jobs(self.batch_id, self.generated_tables, seed_tables)
        outcome = run_load_jobs(self.batch_id)
        if outcome["pending"]:
            raise PipelineError(
                f"台账装载仍有 {outcome['pending']} 个作业未成功，该阶段不可视为就绪"
            )
        legacy_total = sum(len(rows) for rows in seed_tables.values())
        return (f"队列装载完成：{outcome['completed']} 个模块作业提交成功，"
                f"既有种子 {legacy_total} 行按原批次保留，本批样例 "
                f"{sum(len(rows) for rows in self.generated_tables.values())} 行")

    def _stage_reconcile(self, record: dict[str, Any]) -> str:
        outcome = run_reconcile(
            batch_id=self.batch_id,
            seed_version=self.seed_version,
            business_date=self.business_date,
            timezone_name=self.timezone_name,
            generated_deviations=self.generated_deviations,
        )
        record["reconciliation"] = outcome["reconciliation"]
        record["deviation_checksum"] = outcome["deviation_checksum"]
        rec = outcome["reconciliation"]
        if not rec["consistent"]:
            raise PipelineError("核对未通过：汇总页、模块台账与偏离清单批次不一致")
        return (f"核对通过 {rec['reconciliation_id']}：18 个模块台账同批次，"
                f"偏离 {rec['deviation_count']} 条，汇总统计已重算")

    def _stage_verify(self, record: dict[str, Any]) -> str:
        """发布前总校验：三件套批次/核对号一致、检查点齐全、缓存命中。"""
        rec = record.get("reconciliation")
        if not rec or not rec.get("consistent"):
            raise PipelineError("verify 失败：缺少一致的核对结论")
        summary = state_mod.read_json(state_mod.SUMMARY_FILE, {})
        deviations = state_mod.read_json(state_mod.DEVIATION_FILE, {})
        ledgers = state_mod.read_json(state_mod.LEDGER_FILE, {})

        checks = [
            (summary.get("batch_id") == self.batch_id, "汇总页批次不匹配"),
            (deviations.get("batch_id") == self.batch_id, "偏离清单批次不匹配"),
            (ledgers.get("_batch_id") == self.batch_id, "模块台账批次不匹配"),
            (summary.get("reconciliation_id") == rec["reconciliation_id"], "汇总页核对号不匹配"),
            (deviations.get("reconciliation_id") == rec["reconciliation_id"], "偏离清单核对号不匹配"),
            (ledgers.get("_reconciliation_id") == rec["reconciliation_id"], "台账核对号不匹配"),
            (deviations.get("checksum") == rec["deviation_checksum"], "偏离清单校验和不匹配"),
        ]
        failures = [message for ok, message in checks if not ok]
        for module in MODULE_ORDER:
            rows = [row for row in ledgers.get("tables", {}).get(module, [])
                    if row.get("_batch_id") == self.batch_id]
            if table_checksum(rows) != rec["module_checksums"][module]:
                failures.append(f"模块 {module} 台账校验和不匹配")
        if failures:
            raise PipelineError("verify 失败：" + "；".join(failures))

        from app.pipeline.adapters import get_cache_adapter

        cached = get_cache_adapter().cache_get(f"agg:{self.batch_id}")
        if not cached or cached.get("reconciliation_id") != rec["reconciliation_id"]:
            raise PipelineError("verify 失败：汇总统计缓存缺失或核对号不一致")

        # 前置阶段检查点也必须都是 done，防止把未成功阶段当就绪
        # （verify 自身此刻尚未写检查点，publish 只能在 verify 之后执行）
        for stage in STAGES:
            if stage in {"verify", "publish"}:
                continue
            if record["stages"][stage]["status"] != "done":
                failures.append(f"阶段 {stage} 未成功")
        if failures:
            raise PipelineError("verify 失败：" + "；".join(failures))

        snapshot = {
            "batch_id": self.batch_id,
            "seed_version": self.seed_version,
            "business_date": self.business_date,
            "timezone": self.timezone_name,
            "reconciliation": rec,
            "summary": summary,
            "deviations": deviations,
            "current_tables": {
                module: [row for row in ledgers.get("tables", {}).get(module, [])
                         if row.get("_batch_id") == self.batch_id]
                for module in MODULE_ORDER
            },
            "snapshot_checksum": "",
        }
        snapshot["snapshot_checksum"] = digest_payload(
            {k: v for k, v in snapshot.items() if k != "snapshot_checksum"}
        )
        state_mod.atomic_write_json(state_mod.batch_file(self.batch_id), snapshot)
        return f"发布前校验通过，批次快照已固化（{snapshot['snapshot_checksum'][:12]}）"

    def _publish_pointer(self, record: dict[str, Any]) -> None:
        state = state_mod.load_state()
        previous = state.get("current_batch_id")
        state["current_batch_id"] = self.batch_id
        state["seed_version"] = self.seed_version
        state["business_date"] = self.business_date
        state["timezone"] = self.timezone_name
        state["published_at"] = iso(now_local(self.timezone_name), self.timezone_name)
        state["ready"] = True
        history = [h for h in state.get("history", []) if h.get("batch_id") != self.batch_id]
        history.insert(0, {
            "batch_id": self.batch_id,
            "seed_version": self.seed_version,
            "business_date": self.business_date,
            "timezone": self.timezone_name,
            "published_at": state["published_at"],
            "reconciliation_id": record["reconciliation"]["reconciliation_id"],
        })
        state["history"] = history[:20]
        state_mod.save_state(state)
        self._mark(record, "publish", "done",
                   f"当前指针已发布：{previous or '无'} -> {self.batch_id}")


def status_snapshot() -> dict[str, Any]:
    """流水线/批次状态查询：供 /api/pipeline/status 与健康检查使用。"""
    state = state_mod.load_state()
    batch_id = state.get("current_batch_id")
    out: dict[str, Any] = {
        "ready": bool(state.get("ready")),
        "env": os.environ.get("APP_ENV", settings.env) or settings.env,
        "business_timezone": state.get("timezone")
        or os.environ.get("BUSINESS_TIMEZONE") or settings.business_timezone,
        "cache_backend": os.environ.get("CACHE_BACKEND")
        or ("file" if settings.env == "local" else "redis"),
        "queue_backend": os.environ.get("QUEUE_BACKEND")
        or ("file" if settings.env == "local" else "redis"),
        "current_batch_id": batch_id,
        "seed_version": state.get("seed_version"),
        "business_date": state.get("business_date"),
        "published_at": state.get("published_at"),
        "history": state.get("history", []),
        "run": None,
    }
    if batch_id:
        run = state_mod.read_json(state_mod.run_file(batch_id), {})
        out["run"] = {
            "ready": run.get("ready", False),
            "stages": run.get("stages", {}),
            "reconciliation": run.get("reconciliation"),
            "batch": run.get("batch"),
        }
        snapshot_path = state_mod.batch_file(batch_id)
        out["snapshot_available"] = os.path.exists(snapshot_path)
    return out


# --------------------------------------------------------------------------- #
# 回滚
# --------------------------------------------------------------------------- #
def rollback_to_batch(batch_id: str) -> dict[str, Any]:
    """把当前指针回滚到历史批次快照（快照不可变，切换指针即完成回滚）。"""
    with state_mod.run_lock():
        snapshot = state_mod.read_json(state_mod.batch_file(batch_id), None)
        if not snapshot:
            raise PipelineError(f"批次 {batch_id} 没有不可变快照，无法回滚")
        rec = snapshot["reconciliation"]
        # 用快照重建三件套中本批次的视图：台账保留其它批次历史行，仅替换当前批
        ledgers = state_mod.read_json(state_mod.LEDGER_FILE, {})
        tables = ledgers.get("tables", {})
        for module in MODULE_ORDER:
            base = [row for row in tables.get(module, []) if row.get("_batch_id") != batch_id]
            base.extend(copy.deepcopy(snapshot.get("current_tables", {}).get(module, [])))
            base.sort(key=lambda row: int(row.get("id", 0)))
            tables[module] = base
        state_mod.atomic_write_json(state_mod.LEDGER_FILE, {
            "_batch_id": batch_id,
            "_reconciliation_id": rec["reconciliation_id"],
            "_reconciled_at": rec["reconciled_at"],
            "tables": tables,
            "stamps": {
                module: {
                    "batch_id": batch_id,
                    "reconciliation_id": rec["reconciliation_id"],
                    "module": module,
                    "module_label": stamp["module_label"],
                    "created": stamp["created"],
                    "pending": stamp["pending"],
                    "abnormal": stamp["abnormal"],
                    "checksum": rec["module_checksums"][module],
                    "reconciled_at": rec["reconciled_at"],
                }
                for module, stamp in ((s["module"], s) for s in rec["modules"])
            },
        })
        state_mod.atomic_write_json(state_mod.DEVIATION_FILE, snapshot["deviations"])
        state_mod.atomic_write_json(state_mod.SUMMARY_FILE, snapshot["summary"])

        state = state_mod.load_state()
        state["current_batch_id"] = batch_id
        state["seed_version"] = snapshot["seed_version"]
        state["business_date"] = snapshot["business_date"]
        state["timezone"] = snapshot["timezone"]
        state["published_at"] = iso(now_local(snapshot["timezone"]), snapshot["timezone"])
        state["ready"] = True
        state["rollback_to"] = batch_id
        state_mod.save_state(state)
        return {"ok": True, "batch_id": batch_id,
                "reconciliation_id": rec["reconciliation_id"]}


def rollback_to_seed(seed_version: str, business_date: str) -> dict[str, Any]:
    """回滚到指定种子版本重新出批次（旧批次快照保留，可再切回）。"""
    if seed_version not in SEED_VERSIONS:
        raise PipelineError(f"种子版本 {seed_version} 不存在：{SEED_VERSIONS}")
    runner = PipelineRunner(business_date=business_date, seed_version=seed_version)
    runner.run(force=True)
    return {"ok": True, "batch_id": runner.batch_id, "seed_version": seed_version}
