"""勘探设备接口：列表/明细/登记/动作/导出，批次口径由路由工厂统一加盖。"""
from __future__ import annotations

from app.modules import REGISTRY
from app.routers.factory import build_router

router = build_router(REGISTRY["equipment"])
