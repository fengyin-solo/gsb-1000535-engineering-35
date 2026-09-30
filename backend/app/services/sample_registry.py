"""样品登记业务规则：状态流转、字段校验与筛选口径统一收在 BaseService。"""
from __future__ import annotations

from app.services.base import BaseService


class SampleRegistryService(BaseService):
    module = "sample_registry"
    entity = "送检样品"
