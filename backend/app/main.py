"""地质勘探数据管理平台 后端服务入口。

启动：uvicorn app.main:app --host 127.0.0.1 --port 8000
健康检查：GET /api/health

本地首次启动且无已发布批次时，会按业务时区自动执行一遍初始化流水线；
环境变量 AUTO_BOOTSTRAP=0 可关闭（容器部署时用 entrypoint 显式 deploy）。
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.bootstrap import bootstrap_if_needed
from app.config import settings
from app.routers import ROUTERS
from app.store import store

app = FastAPI(title="地质勘探数据管理平台", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for module in ROUTERS:
    app.include_router(module.router)


@app.on_event("startup")
def _startup_pipeline() -> None:
    # 先初始化/续跑流水线，再让内存仓库从发布台账重新装载，
    # 保证首个请求读到的就是已核对批次，而不是内置兜底种子
    bootstrap_if_needed()
    store.reload()


@app.get("/api/health")
def health() -> dict[str, object]:
    """健康检查：服务监听、流水线就绪状态与当前批次一并暴露。"""
    from app.pipeline.runner import status_snapshot

    status = status_snapshot()
    return {
        "ok": bool(status.get("ready")),
        "app": settings.app_name,
        "env": settings.env,
        "business_timezone": settings.business_timezone,
        "modules": len(store.module_names()),
        "pipeline": {
            "ready": status.get("ready"),
            "current_batch_id": status.get("current_batch_id"),
            "seed_version": status.get("seed_version"),
            "business_date": status.get("business_date"),
        },
    }


@app.get("/api/overview")
def overview() -> dict[str, object]:
    """运营概览：汇总页卡片与各模块台账同批次、同核对号。"""
    return store.overview()
