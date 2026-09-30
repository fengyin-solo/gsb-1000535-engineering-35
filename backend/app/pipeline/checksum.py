"""批次标识与校验和工具。

批次 ID 由种子版本 + 业务日期 + 业务时区派生，同一入参重跑得到同一批次，
保证流水线幂等。校验和覆盖批次台账内容，汇总页 / 模块台账 / 偏离清单
必须带上同一个批次 ID 和同一份核对结果。
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any


def make_batch_id(seed_version: str, business_date: str, timezone_name: str) -> str:
    raw = f"{seed_version}|{business_date}|{timezone_name}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"BATCH-{business_date.replace('-', '')}-{digest}"


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def row_checksum(row: dict[str, Any]) -> str:
    """单行内容指纹：只看业务列与批次归类列，不看行内已有的 _checksum。"""
    payload = {k: v for k, v in sorted(row.items()) if k != "_checksum"}
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()[:16]


def table_checksum(rows: list[dict[str, Any]]) -> str:
    """整表指纹：逐行规范化后合并哈希，行顺序不影响结果。"""
    joined = "\n".join(sorted(row_checksum(row) for row in rows))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:24]


def stamp_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """给行集补 _checksum，返回同一批对象（幂等：重复调用结果不变）。"""
    for row in rows:
        row["_checksum"] = row_checksum(row)
    return rows


def digest_payload(payload: Any) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()[:24]


def clone_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return copy.deepcopy(rows)
