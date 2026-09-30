#!/usr/bin/env bash
# 容器/部署入口：先跑可重复流水线，未成功发布绝不启动服务。
#
# 行为：
#   1. 流水线幂等续跑（已 ready 直接确认；有检查点则从断点继续提交）；
#   2. 部署闸门探测缓存/队列并核对当前批次构建清单，任一不满足即阻断；
#   3. 只有批次 ready 才 exec uvicorn，未成功阶段不会让服务“看起来就绪”。
set -euo pipefail
cd "$(dirname "$0")"

AS_OF="${PIPELINE_AS_OF:-}"
AS_OF_ARG=()
if [ -n "${AS_OF}" ]; then
  AS_OF_ARG=(--as-of "${AS_OF}")
fi

echo "[entrypoint] 执行业务数据流水线（BUSINESS_TZ=${BUSINESS_TZ:-Asia/Shanghai}）"
python -m app.pipeline.runner run --no-build --require-manifest "${AS_OF_ARG[@]}"

echo "[entrypoint] 流水线已就绪，启动服务"
exec python -m uvicorn app.main:app --host 0.0.0.0 --port "${APP_PORT:-8000}"
