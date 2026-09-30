"""遥感解译业务规则：状态流转、字段校验与筛选口径统一收在 BaseService。"""
from __future__ import annotations

from app.services.base import BaseService


class RemoteService(BaseService):
    module = "remote"
    entity = "遥感数据"
