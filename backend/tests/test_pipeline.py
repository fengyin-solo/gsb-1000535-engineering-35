"""流水线端到端行为测试：幂等、检查点续跑、闸门阻断、回滚、三方一致。"""
from __future__ import annotations

import json
from datetime import date

import pytest


def _tables(pipeline):
    snapshot = pipeline.state.load_snapshot(pipeline.batch_id)
    assert snapshot is not None
    return snapshot["tables"], snapshot["artifact"]


def test_idempotent_full_run(pipeline):
    """完整跑一遍再重复执行：批次内容、发布历史、补录行数都不增长。"""
    first = pipeline.run()
    assert first["status"] == "ready"

    history_after_first = len(pipeline.state.release_history())
    second = pipeline.run()
    assert second["status"] == "ready"
    assert len(pipeline.state.release_history()) == history_after_first

    tables, artifact = _tables(pipeline)
    for module in ("borehole", "assay", "hydro"):
        batch_rows = [r for r in tables[module] if r["batch_id"] == pipeline.batch_id]
        assert len(batch_rows) == 1  # 每模块恰好一条跨日补录，重跑不重复插入


def test_seed_rows_keep_original_batch(pipeline):
    """既有示例数据按原批次保留，只有新补录属于当前批次。"""
    pipeline.run()
    tables, _ = _tables(pipeline)
    borehole = tables["borehole"]
    seed_rows = [r for r in borehole if r["batch_id"] == "seed-v0001"]
    backfill_rows = [r for r in borehole if r["batch_id"] == pipeline.batch_id]
    assert len(seed_rows) == 3
    assert len(backfill_rows) == 1
    assert seed_rows[0]["record_type"] == "seed"
    assert backfill_rows[0]["record_type"] == "backfill"


def test_cross_day_backfill_in_business_timezone(pipeline):
    """跨日补录：业务发生在前日 23:50，登记在当日 00:10，都按业务时区。"""
    pipeline.run()
    tables, _ = _tables(pipeline)
    row = next(r for r in tables["borehole"] if r["late_entry"])
    assert row["business_date"] == "2026-09-29"
    assert row["occurred_at"] == "2026-09-29T23:50:00+08:00"
    assert row["recorded_at"] == "2026-09-30T00:10:00+08:00"
    assert row["开孔日期"] == "2026-09-29"
    assert row["abnormal"] is True


def test_three_way_same_batch(pipeline):
    """汇总页、各模块台账、偏离清单必须是同一批次，且统计可由台账重算。"""
    pipeline.run()
    tables, artifact = _tables(pipeline)

    reconciliation = artifact["reconciliation"]
    assert reconciliation["matched"] is True
    assert reconciliation["batch_id"] == pipeline.batch_id
    assert reconciliation["sources"] == ["summary", "ledgers", "deviations"]

    summary = artifact["summary"]
    assert summary["batch_id"] == pipeline.batch_id
    for name, ledger in artifact["ledgers"].items():
        assert ledger["batch_id"] == pipeline.batch_id
        assert ledger["batch_consistent"] is True
        assert ledger["matched"] is True
    for item in artifact["deviations"]:
        assert item["batch_id"] == pipeline.batch_id

    # 偏离清单 18 个模块各一条跨日补录
    assert reconciliation["deviation_count"] == 18
    assert reconciliation["late_entry_count"] == 18

    # 统计重算：卡片合计 = 各模块台账相加
    cards = {c["label"]: c["value"] for c in summary["cards"]}
    assert cards["本批次新增"] == sum(row["batch_rows"] for row in artifact["ledgers"].values())
    assert cards["跨日补录"] == 18
    assert cards["异常量"] == 18


def test_failure_is_not_ready_and_resumes(pipeline):
    """连接断开：批次停在 failed、无发布；复位后从检查点继续成功。"""
    result = pipeline.run(simulate_failure="reconcile")
    assert result["status"] == "failed"
    assert pipeline.state.load_release() is None
    assert pipeline.state.stage_status(pipeline.batch_id, "reconcile") == "failed"
    assert pipeline.state.stage_status(pipeline.batch_id, "backfill") == "succeeded"

    # 复位后再次提交，已成功阶段不重放（attempts 不增长）
    resumed = pipeline.run()
    assert resumed["status"] == "ready"
    stages = resumed["stages"]
    assert stages["backfill"]["attempts"] == 1
    assert stages["reconcile"]["attempts"] == 2
    assert pipeline.state.load_release()["batch_id"] == pipeline.batch_id


def test_release_stage_failure_leaves_no_pollution(pipeline):
    """发布阶段失败不得产生发布指针；重放也不得覆盖不同内容的快照。"""
    result = pipeline.run(simulate_failure="release")
    assert result["status"] == "failed"
    assert pipeline.state.load_release() is None

    resumed = pipeline.run()
    assert resumed["status"] == "ready"
    assert len(pipeline.state.release_history()) == 1


def test_gate_blocks_and_enumerates_deps(monkeypatch, state_dir):
    """缓存/队列不可用时闸门必须阻断并逐项枚举。"""
    from app import config
    from app.pipeline import deps

    object.__setattr__(config.settings, "required_cache", ["fail:订单缓存", "file:var/cache"])
    object.__setattr__(config.settings, "required_queue", ["fail:补录队列"])
    gate = deps.run_gate()
    assert gate["blocked"] is True
    refs = {(item["kind"], item["ref"]) for item in gate["unavailable"]}
    assert ("cache", "fail:订单缓存") in refs
    assert ("queue", "fail:补录队列") in refs
    assert len(gate["checks"]) == 3  # 全部依赖都被枚举，不静默跳过

    object.__setattr__(config.settings, "required_cache", ["file:var/cache"])
    object.__setattr__(config.settings, "required_queue", ["file:var/queue"])
    assert deps.run_gate()["blocked"] is False


def test_rollback_to_version(pipeline):
    """先发布两个批次，再回滚到旧版本；回滚目标必须是已成功发布。"""
    from app.pipeline.runner import Pipeline, PipelineError
    from app.pipeline.state import PipelineState

    pipeline.run()  # 2026-09-30

    next_day = Pipeline(PipelineState(pipeline.state.root), business_day=date(2026, 10, 1), build=False)
    next_day.run()
    assert pipeline.state.load_release()["batch_id"] == "batch-2026-10-01"

    record = next_day.rollback("batch-2026-09-30")
    assert record["rollback"] is True
    assert pipeline.state.load_release()["batch_id"] == "batch-2026-09-30"

    with pytest.raises(PipelineError):
        next_day.rollback("batch-1999-01-01")


def test_migrations_applied_once(pipeline):
    """种子迁移版本只登记一次；重跑不会重复登记或改写原批次。"""
    pipeline.run()
    first = pipeline.state.applied_migrations()
    ids = [m["id"] for m in first]
    assert ids == ["0001_seed_lineage", "0002_backfill_flag"]

    pipeline.run()
    second = pipeline.state.applied_migrations()
    assert second == first


def test_gate_blocks_without_manifest_when_required(state_dir, day):
    """部署侧不构建但要求构建清单：缺当前批次清单时闸门阻断。"""
    from app.pipeline.runner import Pipeline
    from app.pipeline.state import PipelineState

    deploy = Pipeline(PipelineState(state_dir), business_day=day,
                      build=False, require_manifest=True)
    result = deploy.run()
    assert result["status"] == "failed"
    assert result["stages"]["gate"]["status"] == "failed"
    assert "构建清单" in result["stages"]["gate"]["detail"]["error"]
    assert PipelineState(state_dir).load_release() is None


def test_store_not_ready_before_pipeline(state_dir, day):
    """未成功发布时服务 not ready，且健康检查能显式表达。"""
    from app.pipeline.state import PipelineState
    from app.pipeline.runner import Pipeline
    from app.store import Store

    fresh = Store.__new__(Store)
    fresh.state = PipelineState(state_dir)
    fresh.bootstrap()
    assert fresh.ready is False
    overview = fresh.overview()
    assert overview["batch_id"] is None

    Pipeline(PipelineState(state_dir), business_day=day, build=False).run()
    fresh.bootstrap()
    assert fresh.ready is True
    assert fresh.overview()["batch_id"] == f"batch-{day.isoformat()}"
