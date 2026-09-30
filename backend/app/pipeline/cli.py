"""流水线命令行入口：本地初始化、构建与部署共用同一条可重复流水线。

用法：
    python -m app.pipeline.cli deploy   --business-date 2026-09-30 [--seed-version v3] [--tz Asia/Shanghai] [--force]
    python -m app.pipeline.cli preflight
    python -m app.pipeline.cli status
    python -m app.pipeline.cli rollback --batch BATCH-...
    python -m app.pipeline.cli rollback-seed --seed-version v2 --business-date 2026-09-30
    python -m app.pipeline.cli recompute

退出码：0 成功/已就绪；2 依赖阻断；1 其它失败。
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from app.config import settings
from app.pipeline.preflight import run_preflight
from app.pipeline.runner import (
    BlockedByDependency,
    PipelineError,
    PipelineRunner,
    rollback_to_batch,
    rollback_to_seed,
    status_snapshot,
)


def _print(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def cmd_deploy(args: argparse.Namespace) -> int:
    runner = PipelineRunner(
        business_date=args.business_date,
        seed_version=args.seed_version,
        timezone_name=args.tz,
    )
    try:
        record = runner.run(force=args.force)
    except BlockedByDependency as exc:
        print(f"[阻断] {exc}", file=sys.stderr)
        _print(run_preflight().as_dict())
        return 2
    except PipelineError as exc:
        print(f"[失败] {exc}", file=sys.stderr)
        return 1
    stages = record["stages"]
    print(f"[就绪] 批次 {runner.batch_id}（{runner.seed_version} / "
          f"{runner.business_date} / {runner.timezone_name}）")
    for stage, info in stages.items():
        print(f"  - {stage:<10} {info['status']:<7} {info['detail']}")
    return 0


def cmd_preflight(_args: argparse.Namespace) -> int:
    report = run_preflight()
    _print(report.as_dict())
    if not report.ok:
        print("[阻断] 缺失/不可用依赖：" + "、".join(report.blocked_names()), file=sys.stderr)
        return 2
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    _print(status_snapshot())
    return 0


def cmd_rollback(args: argparse.Namespace) -> int:
    try:
        result = rollback_to_batch(args.batch)
    except PipelineError as exc:
        print(f"[失败] {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0


def cmd_rollback_seed(args: argparse.Namespace) -> int:
    try:
        result = rollback_to_seed(args.seed_version, args.business_date)
    except PipelineError as exc:
        print(f"[失败] {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0


def cmd_recompute(_args: argparse.Namespace) -> int:
    """对当前批次重跑 reconcile+aggregate（核对结果重新落三件套，幂等）。"""
    from app.pipeline import state as state_mod
    from app.pipeline.reconcile import run_reconcile
    from app.pipeline.samples import generate_samples

    state = state_mod.load_state()
    if not state.get("current_batch_id"):
        print("[失败] 尚无已发布批次可重算", file=sys.stderr)
        return 1
    batch_id, _tables, deviations = generate_samples(
        state["seed_version"], state["business_date"], state["timezone"]
    )
    outcome = run_reconcile(
        batch_id=batch_id,
        seed_version=state["seed_version"],
        business_date=state["business_date"],
        timezone_name=state["timezone"],
        generated_deviations=deviations,
    )
    _print(outcome["reconciliation"])
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pipeline", description="勘探数据流水线")
    sub = parser.add_subparsers(dest="command", required=True)

    p_deploy = sub.add_parser("deploy", help="执行/续跑初始化-构建-部署流水线")
    p_deploy.add_argument("--business-date", required=True, help="业务日期 YYYY-MM-DD（业务时区）")
    p_deploy.add_argument("--seed-version", default=settings.seed_version, choices=["v1", "v2", "v3"])
    p_deploy.add_argument("--tz", default=settings.business_timezone, help="业务时区，默认取 BUSINESS_TIMEZONE")
    p_deploy.add_argument("--force", action="store_true", help="忽略已完成检查点强制重跑")
    p_deploy.set_defaults(func=cmd_deploy)

    p_pre = sub.add_parser("preflight", help="部署前依赖探测（失败阻断发布）")
    p_pre.set_defaults(func=cmd_preflight)

    p_status = sub.add_parser("status", help="查看流水线与当前批次状态")
    p_status.set_defaults(func=cmd_status)

    p_rb = sub.add_parser("rollback", help="回滚到历史批次快照")
    p_rb.add_argument("--batch", required=True)
    p_rb.set_defaults(func=cmd_rollback)

    p_rbs = sub.add_parser("rollback-seed", help="回滚到指定种子版本重新出批次")
    p_rbs.add_argument("--seed-version", required=True, choices=["v1", "v2", "v3"])
    p_rbs.add_argument("--business-date", required=True)
    p_rbs.set_defaults(func=cmd_rollback_seed)

    p_agg = sub.add_parser("recompute", help="对当前批次重算汇总统计并重新核对")
    p_agg.set_defaults(func=cmd_recompute)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
