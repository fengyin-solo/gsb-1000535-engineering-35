"""启动引导：本地首次启动且没有已发布批次时，自动跑一遍流水线。

只在 AUTO_BOOTSTRAP 打开、当前没有 ready 批次时触发；已有批次时严格不重跑，
避免污染既有数据。业务日期默认取业务时区“今天”，也可用 BUSINESS_DATE
固定，保证每天构建结果可复现。
"""
from __future__ import annotations

import os

from app.config import settings
from app.pipeline import state as state_mod
from app.pipeline.runner import PipelineError, PipelineRunner, status_snapshot
from app.pipeline.time_utils import date_str, now_local


def bootstrap_if_needed(*, verbose: bool = True) -> dict[str, object] | None:
    state = state_mod.load_state()
    if state.get("ready") and state.get("current_batch_id"):
        return None
    if not settings.auto_bootstrap:
        return None
    business_date = os.environ.get("BUSINESS_DATE", "").strip() or date_str(now_local())
    if verbose:
        print(f"[bootstrap] 未发现已发布批次，按 {settings.business_timezone} "
              f"执行业务日 {business_date} 的初始化流水线 ...")
    runner = PipelineRunner(business_date=business_date)
    record = runner.run()
    if verbose:
        for stage, info in record["stages"].items():
            print(f"[bootstrap]   {stage:<10} {info['status']:<7} {info['detail']}")
        if not record.get("ready"):
            raise PipelineError("初始化流水线未就绪")
    return status_snapshot()
