"""化验数据业务规则：状态流转、字段校验与筛选口径统一收在 BaseService。"""
from __future__ import annotations

from app.services.base import BaseService


class AssayService(BaseService):
    module = "assay"
    entity = "化验结果"
