"""钻孔编录业务规则：状态流转、字段校验与筛选口径统一收在 BaseService。"""
from __future__ import annotations

from app.services.base import BaseService


class BoreholeService(BaseService):
    module = "borehole"
    entity = "钻孔"
