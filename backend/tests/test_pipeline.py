"""流水线测试：用临时 DATA_DIR 跑端到端场景。

覆盖：确定性批次/时区跨日、幂等重跑、失败注入与检查点续跑、
依赖阻断枚举、核对三件套一致性、回滚、种子迁移与既有批次保留、
运行期 HTTP 接口与重算。
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
import urllib.request
import uuid

# 所有配置必须在导入 app.* 前设置
_TMP = tempfile.mkdtemp(prefix="geo-pipeline-tests-")
os.environ["DATA_DIR"] = _TMP
os.environ["AUTO_BOOTSTRAP"] = "0"
os.environ["BUSINESS_TIMEZONE"] = "Asia/Shanghai"
os.environ["CACHE_BACKEND"] = "file"
os.environ["QUEUE_BACKEND"] = "file"
os.environ["DEP_ATTEMPTS"] = "1"
os.environ["DEP_TIMEOUT_SECONDS"] = "1"

from app.pipeline import state as state_mod  # noqa: E402
from app.pipeline.checksum import make_batch_id  # noqa: E402
from app.pipeline.constants import MODULE_ORDER  # noqa: E402
from app.pipeline.runner import (  # noqa: E402
    BlockedByDependency,
    PipelineError,
    PipelineRunner,
    rollback_to_batch,
    status_snapshot,
)
from app.pipeline.seeds import seed_registry  # noqa: E402


def fresh_dir() -> str:
    path = os.path.join(_TMP, "case-" + uuid.uuid4().hex[:10])
    os.makedirs(path, exist_ok=True)
    os.environ["DATA_DIR"] = path
    return path


def read(rel: str) -> dict:
    with open(os.path.join(os.environ["DATA_DIR"], rel), encoding="utf-8") as handle:
        return json.load(handle)


def run_deploy(date: str = "2026-09-30", *, force: bool = False,
               tz: str | None = None, version: str | None = None):
    return PipelineRunner(business_date=date, seed_version=version,
                          timezone_name=tz).run(force=force)


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        fresh_dir()

    def test_01_deterministic_batch_and_timezone_late_entry(self) -> None:
        """同入参批次 ID 恒定；跨日补录按业务时区生成且时间戳带正确偏移。"""
        bid_sh = make_batch_id("v3", "2026-09-30", "Asia/Shanghai")
        record = run_deploy()
        self.assertEqual(record["batch_id"], bid_sh)
        ledgers = read("ledgers.json")
        late = next(r for r in ledgers["tables"]["borehole"]
                    if r.get("_entry_class") == "late_entry")
        regular = next(r for r in ledgers["tables"]["borehole"]
                       if r.get("_entry_class") == "regular")
        self.assertEqual(late["开孔日期"], "2026-09-29")  # 数据发生在前一自然日
        self.assertEqual(late["_recorded_at"], "2026-09-30T09:15:00+08:00")
        self.assertEqual(regular["开孔日期"], "2026-09-30")
        self.assertTrue(late["abnormal"])
        deviations = read("deviations.json")
        self.assertEqual(deviations["total"], 18)
        self.assertTrue(all(i["kind"] == "cross_day_late_entry" for i in deviations["items"]))

    def test_02_timezone_switch_uses_business_config(self) -> None:
        """环境转换以业务时区配置为准：洛杉矶批次偏移 -07:00，ID 不同。"""
        run_deploy(tz="America/Los_Angeles")
        ledgers = read("ledgers.json")
        late = next(r for r in ledgers["tables"]["borehole"]
                    if r.get("_entry_class") == "late_entry")
        self.assertEqual(late["_recorded_at"], "2026-09-30T09:15:00-07:00")
        self.assertEqual(
            state_mod.load_state()["current_batch_id"],
            make_batch_id("v3", "2026-09-30", "America/Los_Angeles"),
        )

    def test_03_idempotent_rerun_no_data_pollution(self) -> None:
        """重复执行幂等：行不翻倍、检查和不变、阶段全部 skipped/done。"""
        first = run_deploy()
        ledgers_1 = read("ledgers.json")
        summary_1 = read("summary.json")
        second = run_deploy()
        ledgers_2 = read("ledgers.json")
        summary_2 = read("summary.json")
        for module in MODULE_ORDER:
            ids_1 = [r["id"] for r in ledgers_1["tables"][module]]
            ids_2 = [r["id"] for r in ledgers_2["tables"][module]]
            self.assertEqual(ids_1, ids_2, f"{module} 重跑后行数/顺序被污染")
        self.assertEqual(
            ledgers_1["stamps"]["borehole"]["checksum"],
            ledgers_2["stamps"]["borehole"]["checksum"],
        )
        self.assertTrue(second["ready"])
        self.assertTrue(all(v["status"] == "done" for v in second["stages"].values()))

    def test_04_legacy_seed_keeps_original_batch(self) -> None:
        """既有示例数据按原批次 seed-v1 保留，迁移版本字段演进但批次不改。"""
        run_deploy(version="v3")
        ledgers = read("ledgers.json")
        legacy = [r for r in ledgers["tables"]["borehole"] if r["_batch_id"] == "seed-v1"]
        self.assertEqual(len(legacy), 3)
        self.assertTrue(all(r["_seed_version"] == "v3" for r in legacy))
        self.assertTrue(all(r.get("_ref_code", "").startswith("REF-BORE") for r in legacy))
        self.assertTrue(all(r["钻孔编号"].startswith("BORE-000") for r in legacy))

    def test_05_three_artifacts_share_same_batch_and_reconciliation(self) -> None:
        """核对结果同时落到汇总页、模块台账、偏离清单并驱动统计重算。"""
        run_deploy()
        ledgers = read("ledgers.json")
        summary = read("summary.json")
        deviations = read("deviations.json")
        bid = state_mod.load_state()["current_batch_id"]
        rec = f"REC-{bid}"
        self.assertEqual(ledgers["_batch_id"], bid)
        self.assertEqual(summary["batch_id"], bid)
        self.assertEqual(deviations["batch_id"], bid)
        self.assertEqual(ledgers["_reconciliation_id"], rec)
        self.assertEqual(summary["reconciliation_id"], rec)
        self.assertEqual(deviations["reconciliation_id"], rec)
        self.assertEqual(deviations["checksum"],
                         summary["reconciliation"]["deviation_checksum"])
        for module in MODULE_ORDER:
            stamp = ledgers["stamps"][module]
            agg = next(m for m in summary["modules"] if m["name"] == module)
            self.assertEqual(stamp["batch_id"], bid)
            self.assertEqual(stamp["checksum"], agg["checksum"])
            self.assertEqual(stamp["created"], agg["created"])
        # 统计卡片由核对阶段重算
        cards = {c["label"]: c["value"] for c in summary["cards"]}
        self.assertEqual(cards["本批新增"], 36)
        self.assertEqual(cards["偏离条数"], 18)
        self.assertEqual(cards["台账总量（含既有批次）"], 54 + 36)

    def test_06_failed_stage_blocks_ready_and_resumes(self) -> None:
        """未成功阶段不当就绪；复位后从检查点再次提交，不重复污染。"""
        os.environ["FAIL_STAGE"] = "load"
        with self.assertRaises(PipelineError):
            PipelineRunner(business_date="2026-09-30").run()
        del os.environ["FAIL_STAGE"]
        # 未就绪
        self.assertFalse(state_mod.load_state().get("ready"))
        run = state_mod.read_json(
            state_mod.run_file(make_batch_id("v3", "2026-09-30", "Asia/Shanghai")), {}
        )
        self.assertEqual(run["stages"]["load"]["status"], "pending")
        self.assertEqual(run["stages"]["preflight"]["status"], "done")
        # 复位后续跑
        record = run_deploy()
        self.assertTrue(record["ready"])
        ledgers = read("ledgers.json")
        self.assertEqual(len(ledgers["tables"]["borehole"]), 5)  # 3 既有 + 2 本批

    def test_07_preflight_blocks_and_enumerates_dependencies(self) -> None:
        """缓存/队列不可用时阻断发布并枚举依赖。"""
        os.environ["CACHE_BACKEND"] = "redis"
        os.environ["QUEUE_BACKEND"] = "redis"
        os.environ["REDIS_URL"] = "redis://127.0.0.1:6399/0"
        try:
            from app.pipeline.preflight import run_preflight

            report = run_preflight()
            self.assertFalse(report.ok)
            names = " ".join(report.blocked_names())
            self.assertIn("缓存", names)
            self.assertIn("队列", names)
            with self.assertRaises(BlockedByDependency):
                PipelineRunner(business_date="2026-09-30").run()
        finally:
            os.environ["CACHE_BACKEND"] = "file"
            os.environ["QUEUE_BACKEND"] = "file"

    def test_08_batch_rollback_restores_pointers(self) -> None:
        """版本/批次回滚：指针切回历史快照，历史批次行不丢。"""
        run_deploy("2026-09-29")
        first_bid = state_mod.load_state()["current_batch_id"]
        run_deploy("2026-09-30")
        second_bid = state_mod.load_state()["current_batch_id"]
        self.assertNotEqual(first_bid, second_bid)
        result = rollback_to_batch(first_bid)
        self.assertEqual(result["batch_id"], first_bid)
        ledgers = read("ledgers.json")
        deviations = read("deviations.json")
        self.assertEqual(ledgers["_batch_id"], first_bid)
        self.assertEqual(deviations["batch_id"], first_bid)
        # 第二批次历史行仍在台账里
        ids = {(r["id"], r["_batch_id"]) for r in ledgers["tables"]["borehole"]}
        self.assertIn((1001, second_bid), ids)
        self.assertTrue(state_mod.load_state()["ready"])

    def test_09_seed_migrations_are_deterministic(self) -> None:
        """种子迁移是版本纯函数：重复构造同内容；v1/v2/v3 校验和稳定。"""
        a = seed_registry.checksum("v3")
        b = seed_registry.checksum("v3")
        self.assertEqual(a, b)
        v2 = seed_registry.version("v2")
        v3 = seed_registry.version("v3")
        self.assertNotIn("_ref_code", v2["borehole"][0])
        self.assertIn("_ref_code", v3["borehole"][0])
        self.assertEqual(v2["borehole"][0]["_batch_id"], "seed-v1")
        self.assertIsNotNone(v2["borehole"][0]["_recorded_at"])

    def test_09b_interrupted_jobs_are_reset_and_resubmitted(self) -> None:
        """连接断开复位：running 作业回到 pending，重跑从检查点再次提交。"""
        from app.pipeline.adapters import get_queue_adapter
        from app.pipeline.loading import run_load_jobs, seed_load_jobs
        from app.pipeline.samples import generate_samples

        batch_id, generated, _dev = generate_samples("v3", "2026-09-30", "Asia/Shanghai")
        seed_tables = seed_registry.version("v3", "Asia/Shanghai")
        seed_load_jobs(batch_id, generated, seed_tables)
        queue = get_queue_adapter()
        # 认领 5 个作业但不完成，模拟提交进程断开
        claimed = [queue.claim(batch_id) for _ in range(5)]
        self.assertTrue(all(job and job["status"] == "running" for job in claimed))
        # 立即复位场景：手工把 running 标回 pending（复位动作）
        jobs = state_mod.read_json(state_mod.QUEUE_FILE, [])
        for job in jobs:
            if job["status"] == "running":
                job["status"] = "pending"
                job.pop("claimed_at", None)
        state_mod.atomic_write_json(state_mod.QUEUE_FILE, jobs)
        self.assertEqual(queue.pending_count(batch_id), 18)
        outcome = run_load_jobs(batch_id)
        self.assertEqual((outcome["claimed"], outcome["completed"], outcome["pending"]),
                         (18, 18, 0))

    def test_09c_recompute_is_idempotent(self) -> None:
        """recompute 重复执行：批次与统计不漂移。"""
        run_deploy()
        from app.pipeline.reconcile import run_reconcile
        from app.pipeline.samples import generate_samples

        state = state_mod.load_state()
        _bid, _tables, dev = generate_samples(
            state["seed_version"], state["business_date"], state["timezone"])
        r1 = run_reconcile(
            batch_id=state["current_batch_id"], seed_version=state["seed_version"],
            business_date=state["business_date"], timezone_name=state["timezone"],
            generated_deviations=dev)
        r2 = run_reconcile(
            batch_id=state["current_batch_id"], seed_version=state["seed_version"],
            business_date=state["business_date"], timezone_name=state["timezone"],
            generated_deviations=dev)
        self.assertEqual(r1["reconciliation"]["reconciliation_id"],
                         r2["reconciliation"]["reconciliation_id"])
        self.assertEqual(r1["reconciliation"]["ledger_checksum"],
                         r2["reconciliation"]["ledger_checksum"])
        ledgers = read("ledgers.json")
        self.assertEqual(len(ledgers["tables"]["borehole"]), 5)

    def test_10_http_server_serves_consistent_overview(self) -> None:
        """真实 HTTP 启动：health/overview/deviations/ledger 同批次同核对号。"""
        run_deploy()
        import socket as socket_mod

        import uvicorn

        from app.main import app
        from app.store import store

        store.reload()
        sock = socket_mod.socket(socket_mod.AF_INET, socket_mod.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, args=([sock],), daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{port}"
            for _ in range(50):
                try:
                    urllib.request.urlopen(base + "/api/health", timeout=1)
                    break
                except OSError:
                    time.sleep(0.1)

            def get(path: str) -> dict:
                with urllib.request.urlopen(base + path, timeout=5) as resp:
                    return json.loads(resp.read())

            health = get("/api/health")
            self.assertTrue(health["ok"])
            overview = get("/api/overview")
            deviations = get("/api/deviations")
            ledger = get("/api/ledger")
            bid = health["pipeline"]["current_batch_id"]
            self.assertEqual(overview["batch_id"], bid)
            self.assertEqual(deviations["batch_id"], bid)
            self.assertEqual(ledger["batch_id"], bid)
            self.assertEqual(overview["reconciliation_id"], deviations["reconciliation_id"])
            self.assertEqual(overview["reconciliation_id"], ledger["reconciliation_id"])
            one = get("/api/ledger/borehole")
            self.assertEqual(one["stamp"]["batch_id"], bid)
            self.assertEqual(one["total"], 5)
            # 流水线状态接口
            status = get("/api/pipeline/status")
            self.assertTrue(status["ready"])
            self.assertEqual(status["run"]["stages"]["publish"]["status"], "done")
        finally:
            server.should_exit = True
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
