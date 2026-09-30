"""地球物理业务规则：状态流转、字段校验与筛选口径统一收在 BaseService。"""
from __future__ import annotations

from app.services.base import BaseService


class GeophysicsService(BaseService):
    module = "geophysics"
    entity = "物探测线"
