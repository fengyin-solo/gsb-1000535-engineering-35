#!/usr/bin/env bash
# 本地开发启动：先幂等执行数据流水线（含跨日补录与三方对账），再启动服务。
# 流水线失败时立即退出，避免在“未就绪”数据上调试。
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
.venv/bin/pip install -q -r requirements.txt

export PIPELINE_STATE_DIR="${PIPELINE_STATE_DIR:-var/pipeline}"
export BUSINESS_TZ="${BUSINESS_TZ:-Asia/Shanghai}"

.venv/bin/python -m app.pipeline.runner run --no-build
exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
