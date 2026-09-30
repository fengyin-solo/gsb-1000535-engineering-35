"""地层划分业务规则：状态流转、字段校验与筛选口径统一收在 BaseService。"""
from __future__ import annotations

from app.services.base import BaseService


class StratigraphyService(BaseService):
    module = "stratigraphy"
    entity = "地层单元"
