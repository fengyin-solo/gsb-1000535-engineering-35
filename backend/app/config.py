"""运行配置：环境、业务时区、流水线状态目录与部署依赖都从环境变量读取。

环境转换（local/staging/prod）只改环境变量，不改代码；业务时区以
BUSINESS_TZ 为唯一准绳，流水线生成跨日补录与汇总重算都按它切日。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _csv(value: str | None, default: list[str]) -> list[str]:
    if not value:
        return list(default)
    return [part.strip() for part in value.split(",") if part.strip()]


@dataclass(frozen=True)
class Settings:
    app_name: str = "地质勘探数据管理平台"
    app_version: str = field(default_factory=lambda: os.getenv("APP_VERSION", "1.0.0"))
    # local / staging / prod：只影响默认依赖清单，不改变业务口径
    env: str = field(default_factory=lambda: os.getenv("APP_ENV", "local"))
    port: int = field(default_factory=lambda: int(os.getenv("APP_PORT", "8000")))
    # 业务时区：跨日补录与“今日新增”按此时区切日，默认中国标准时间（固定 UTC+8，无夏令时）
    business_tz: str = field(default_factory=lambda: os.getenv("BUSINESS_TZ", "Asia/Shanghai"))
    # 流水线状态根目录（检查点、批次快照、发布指针、迁移版本都在里面）
    state_dir: Path = field(default_factory=lambda: Path(os.getenv("PIPELINE_STATE_DIR", "var/pipeline")))
    page_size_default: int = 20
    page_size_max: int = field(default_factory=lambda: int(os.getenv("PAGE_SIZE_MAX", "200")))
    allowed_origins: list[str] = field(
        default_factory=lambda: _csv(
            os.getenv("ALLOWED_ORIGINS"),
            ["http://127.0.0.1:5173", "http://localhost:5173"],
        )
    )
    # 部署前必须可用的依赖；缓存/队列不可用时 gate 阻断发布并逐项枚举。
    # 本地默认用状态目录下的文件型探针，零外部中间件即可起；staging/prod
    # 通过环境变量换成 redis://、amqp:// 等真实地址。
    required_cache: list[str] = field(
        default_factory=lambda: _csv(os.getenv("REQUIRED_CACHE"), ["file:var/cache"])
    )
    required_queue: list[str] = field(
        default_factory=lambda: _csv(os.getenv("REQUIRED_QUEUE"), ["file:var/queue"])
    )
    # 连接断开后流水线重试设置：次数与退避基准秒
    retry_attempts: int = field(default_factory=lambda: int(os.getenv("PIPELINE_RETRY", "3")))
    retry_backoff_seconds: float = field(
        default_factory=lambda: float(os.getenv("PIPELINE_RETRY_BACKOFF", "0.05"))
    )

    @property
    def is_production(self) -> bool:
        return self.env.lower() in {"prod", "production"}


settings = Settings()
