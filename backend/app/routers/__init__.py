"""业务模块路由汇总：以模块台账注册表为唯一来源装配 18 个模块路由。"""
from __future__ import annotations

from app.modules import REGISTRY
from app.routers import (
    assay, borehole, core, drilling_log, environmental, equipment, geochem,
    geological_report, geophysics, hydro, mapping, mineral, remote, reserve,
    sample_registry, section, stratigraphy, survey_point,
)

# 兼容旧代码的命名：每个模块仍以 app.routers.<module>.router 暴露
_MODULE_ROUTERS = [
    borehole, core, stratigraphy, geophysics, geochem, assay, mapping,
    survey_point, drilling_log, reserve, sample_registry, equipment, hydro,
    section, geological_report, remote, mineral, environmental,
]

# 工厂产出的路由（与各薄模块文件里的 router 等价，这里统一装配一次）
ROUTERS = list(_MODULE_ROUTERS)
