"""流水线测试公共夹具：临时状态目录、隔离环境、HTTP 客户端。"""
from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from datetime import date
from pathlib import Path

# 必须在 import 应用模块前固定环境变量
_TMP = tempfile.mkdtemp(prefix="pipeline-tests-")
os.environ["PIPELINE_STATE_DIR"] = _TMP
os.environ["BUSINESS_TZ"] = "Asia/Shanghai"
os.environ["REQUIRED_CACHE"] = "file:var/cache"
os.environ["REQUIRED_QUEUE"] = "file:var/queue"
os.environ["PIPELINE_RETRY"] = "3"
os.environ["PIPELINE_RETRY_BACKOFF"] = "0"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def state_dir(tmp_path: Path) -> Iterator[Path]:
    """每个用例一个全新状态目录，并让全局配置指过去。"""
    target = tmp_path / "pipeline"
    target.mkdir()
    os.environ["PIPELINE_STATE_DIR"] = str(target)
    # settings 是冻结单例，测试里直接替换其引用对象
    from app import config

    object.__setattr__(config.settings, "state_dir", target)
    yield target
    os.environ["PIPELINE_STATE_DIR"] = _TMP


@pytest.fixture()
def day() -> date:
    return date(2026, 9, 30)


@pytest.fixture()
def pipeline(state_dir, day):
    from app.pipeline.runner import Pipeline
    from app.pipeline.state import PipelineState

    return Pipeline(PipelineState(state_dir), business_day=day, build=False)


@pytest.fixture()
def client(state_dir):
    # store 在 import 时已 bootstrap，用例需要按新状态目录重新装载
    from app import main
    from app.store import store

    store.state = type(store.state)(state_dir)
    store.bootstrap()
    return TestClient(main.app)
