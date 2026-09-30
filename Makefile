.PHONY: install backend frontend \
        preflight deploy status recompute rollback rollback-seed test \
        docker-local docker-deploy docker-preflight build-frontend

# 流水线参数（可覆盖：make deploy BUSINESS_DATE=2026-09-30 TZ=Asia/Shanghai）
BUSINESS_DATE ?= $(shell TZ="$${BUSINESS_TIMEZONE:-Asia/Shanghai}" date +%F)
BUSINESS_TIMEZONE ?= Asia/Shanghai
SEED_VERSION ?= v3
DATA_DIR ?= $(CURDIR)/var/data

PIPE = cd backend && DATA_DIR="$(DATA_DIR)" BUSINESS_TIMEZONE="$(BUSINESS_TIMEZONE)" \
       AUTO_BOOTSTRAP=0 python3 -m app.pipeline.cli

install:
	cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
	cd frontend && npm install

backend:
	cd backend && ./run.sh

frontend:
	cd frontend && npm run dev

# ---- 可重复流水线：本地初始化 / 构建 / 部署同一入口，幂等可重跑 ----
preflight:
	@$(PIPE) preflight

# 全量流水线：preflight -> migrate -> generate -> load -> reconcile -> verify -> publish
deploy:
	@$(PIPE) deploy --business-date "$(BUSINESS_DATE)" --seed-version "$(SEED_VERSION)" --tz "$(BUSINESS_TIMEZONE)"

# 忽略检查点强制重跑（不污染数据：装载与核对都是 upsert）
redeploy:
	@$(PIPE) deploy --business-date "$(BUSINESS_DATE)" --seed-version "$(SEED_VERSION)" --tz "$(BUSINESS_TIMEZONE)" --force

status:
	@$(PIPE) status

# 对当前批次重新核对并驱动汇总统计重算
recompute:
	@$(PIPE) recompute

# 回滚到历史批次不可变快照
rollback:
	@test -n "$(BATCH)" || { echo "用法: make rollback BATCH=BATCH-xxxx"; exit 1; }
	@$(PIPE) rollback --batch "$(BATCH)"

# 回滚种子版本并重新出批次
rollback-seed:
	@test -n "$(VERSION)" || { echo "用法: make rollback-seed VERSION=v2 BUSINESS_DATE=2026-09-30"; exit 1; }
	@$(PIPE) rollback-seed --seed-version "$(VERSION)" --business-date "$(BUSINESS_DATE)"

test:
	cd backend && python3 -m unittest discover -s tests -v

build-frontend:
	cd frontend && npm run build

# ---- 容器构建与部署 ----
docker-local:
	docker compose --profile local up --build

docker-preflight:
	docker compose -f docker-compose.yml -f docker-compose.deploy.yml \
	  --profile deploy run --rm backend python -m app.pipeline.cli preflight

docker-deploy:
	docker compose -f docker-compose.yml -f docker-compose.deploy.yml \
	  --profile deploy up --build -d
