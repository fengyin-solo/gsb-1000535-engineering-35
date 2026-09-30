"""运行配置：端口、跨域、运行环境与流水线参数。

所有部署相关的开关都从环境变量读取，默认值面向本地开发；
环境切换（local/staging/prod）时只改环境变量，业务时区以
BUSINESS_TIMEZONE 为准，容器的 TZ 只影响系统调用，不影响业务口径。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _default_backend(kind: str) -> str:
    """依赖后端默认值：本地走文件实现，staging/prod 走 Redis。

    kind 取 cache / queue。可以用 CACHE_BACKEND / QUEUE_BACKEND 显式覆盖。
    """
    explicit = os.environ.get(f"{kind.upper()}_BACKEND")
    if explicit:
        return explicit.strip()
    return "file" if _env("APP_ENV", "local") == "local" else "redis"


@dataclass(frozen=True)
class Settings:
    app_name: str = "地质勘探数据管理平台"
    env: str = field(default_factory=lambda: _env("APP_ENV", "local"))
    port: int = field(default_factory=lambda: int(_env("APP_PORT", "8000")))
    # 业务时区：跨日补录、批次口径、汇总统计全部按此时区切日
    business_timezone: str = field(default_factory=lambda: _env("BUSINESS_TIMEZONE", "Asia/Shanghai"))
    # 流水线持久化目录（台账/快照/队列/缓存/检查点都放这里）
    data_dir: str = field(default_factory=lambda: _env("DATA_DIR", os.path.join(os.getcwd(), "var", "data")))
    cache_backend: str = field(default_factory=lambda: _default_backend("cache"))
    queue_backend: str = field(default_factory=lambda: _default_backend("queue"))
    redis_url: str = field(default_factory=lambda: _env("REDIS_URL", "redis://redis:6379/0"))
    # 部署前探测依赖的超时与重试次数（连接断开复位后据此重试）
    dep_timeout_seconds: float = field(default_factory=lambda: float(_env("DEP_TIMEOUT_SECONDS", "2")))
    dep_attempts: int = field(default_factory=lambda: int(_env("DEP_ATTEMPTS", "3")))
    # 本地起服务时若还没有任何已发布批次，自动跑一遍初始化流水线
    auto_bootstrap: bool = field(default_factory=lambda: _bool("AUTO_BOOTSTRAP", True))
    seed_version: str = field(default_factory=lambda: _env("SEED_VERSION", "v3"))
    allowed_origins: list[str] = field(
        default_factory=lambda: [
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        ]
    )
    page_size_default: int = 20
    page_size_max: int = 200

    @property
    def is_prod_like(self) -> bool:
        return self.env in {"staging", "prod", "production"}


def reload_settings() -> "Settings":
    """按当前环境变量重建全局配置（测试 / entrypoint 切换环境时使用）。"""
    global settings
    settings = Settings()
    return settings


settings = Settings()
