"""地质勘探数据管理平台 后端服务入口。

启动：uvicorn app.main:app --host 127.0.0.1 --port 8000
健康检查：GET /api/health

健康检查显式区分 listening 与 ready：只有流水线成功发布过批次才 ready，
未成功阶段不会让服务被探针误判为就绪。
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import ROUTERS
from app.routers import pipeline as pipeline_router
from app.store import store

app = FastAPI(title=settings.app_name, version=settings.app_version)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for module in ROUTERS:
    app.include_router(module.router)
app.include_router(pipeline_router.router)


@app.get("/api/health")
def health() -> dict[str, object]:
    """健康检查：端口在听不等于就绪；以当前发布批次为准。"""
    release = store.release_info()
    return {
        "ok": True,
        "listening": True,
        "ready": store.ready,
        "app": settings.app_name,
        "version": settings.app_version,
        "env": settings.env,
        "timezone": settings.business_tz,
        "modules": len(store.module_names()),
        "batch_id": release.get("batch_id") if release else None,
    }


@app.get("/api/overview")
def overview() -> dict[str, object]:
    """运营概览：汇总卡片、各模块计数与对账结论，均取当前发布批次。"""
    return store.overview()
