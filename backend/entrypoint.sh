#!/usr/bin/env bash
# 容器入口：先 preflight 再执行/续跑流水线，全部成功后才启动服务。
# 退出码：0 成功；2 依赖阻断；1 流水线失败。未成功绝不启动对外服务。
set -euo pipefail
cd "$(dirname "$0")"

BUSINESS_DATE="${BUSINESS_DATE:-$(TZ="${BUSINESS_TIMEZONE:-Asia/Shanghai}" date +%F)}"

echo "== [entrypoint] 部署前依赖探测（env=${APP_ENV:-local}） =="
python -m app.pipeline.cli preflight

echo "== [entrypoint] 执行/续跑初始化流水线 business_date=${BUSINESS_DATE} =="
python -m app.pipeline.cli deploy --business-date "${BUSINESS_DATE}"

echo "== [entrypoint] 流水线就绪，启动服务 =="
exec "$@"
