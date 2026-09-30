.PHONY: install test backend frontend pipeline pipeline-fresh gate build deploy rollback status

# 业务时区：所有环境都以该配置为切日准绳
BUSINESS_TZ ?= Asia/Shanghai
export BUSINESS_TZ
# 用绝对路径：各 target 的工作目录不同（frontend/ 与 backend/），相对路径会拼错
PIPELINE_STATE_DIR ?= $(abspath backend/var/pipeline)
export PIPELINE_STATE_DIR

install:
	cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
	cd frontend && npm install

test:
	cd backend && .venv/bin/python -m pytest tests/ -q
	cd frontend && npm run build

backend:
	cd backend && ./run.sh

frontend:
	cd frontend && npm run dev

# 可重复流水线：初始化（种子迁移）-> 跨日补录 -> 三方对账 -> 复核
pipeline:
	cd backend && .venv/bin/python -m app.pipeline.runner run --no-build

# 含前端构建与部署闸门的完整流水线：
# 构建由流水线 build 阶段执行一次（写批次清单），避免额外的 make build
# 先用 vite 清空 dist、又因批次已 ready 跳过构建而丢掉清单。
deploy:
	cd backend && .venv/bin/python -m app.pipeline.runner run

build:
	cd frontend && npm run build

gate:
	cd backend && .venv/bin/python -m app.pipeline.runner gate

status:
	cd backend && .venv/bin/python -m app.pipeline.runner status

# 版本回滚：make rollback BATCH=batch-2026-09-29
rollback:
	cd backend && .venv/bin/python -m app.pipeline.runner rollback "$(BATCH)"
