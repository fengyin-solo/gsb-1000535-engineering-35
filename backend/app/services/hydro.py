"""水文地质业务规则：状态流转、字段校验与筛选口径统一收在 BaseService。"""
from __future__ import annotations

from app.services.base import BaseService


class HydroService(BaseService):
    module = "hydro"
    entity = "水文观测点"
