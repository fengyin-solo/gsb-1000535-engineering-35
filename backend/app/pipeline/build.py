"""构建阶段：执行前端构建并产出带批次号的构建清单。

清单写到前端 dist 与流水线状态目录两处：部署闸门核对清单存在且属于当前
批次，防止拿旧产物冒充新发布。构建失败会原样抛出，由编排器记为 failed。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from typing import Any

from app import clock
from app.config import settings

# app/pipeline/build.py -> parents[0]=pipeline, [1]=app, [2]=backend, [3]=仓库根
REPO_ROOT = Path(__file__).resolve().parents[3]
FRONTEND_DIR = REPO_ROOT / "frontend"
DIST_DIR = FRONTEND_DIR / "dist"
MANIFEST_NAME = "build-manifest.json"


def _fingerprint(directory: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    if not directory.exists():
        return files
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.name == MANIFEST_NAME:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        files[str(path.relative_to(directory))] = digest
    return files


def run_build(batch_id: str, *, offline: bool = False) -> dict[str, Any]:
    """执行 npm run build，返回构建清单。可重复执行，产物内容确定。"""
    if not FRONTEND_DIR.exists():
        raise RuntimeError(f"前端目录不存在：{FRONTEND_DIR}")
    env = dict(os.environ)
    command = ["npm", "run", "build"]
    if offline:
        command = ["npm", "run", "build", "--", "--offline"]
    completed = subprocess.run(
        command,
        cwd=FRONTEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"前端构建失败：{completed.stdout[-800:]}\n{completed.stderr[-800:]}")

    fingerprints = _fingerprint(DIST_DIR)
    manifest = {
        "batch_id": batch_id,
        "app_version": settings.app_version,
        "env": settings.env,
        "built_at": clock.iso_ts(),
        "dist_dir": str(DIST_DIR),
        "asset_count": len(fingerprints),
        "assets": fingerprints,
    }
    from app.pipeline.state import PipelineState

    state = PipelineState().ensure()
    state._atomic_write_json(state.root / MANIFEST_NAME, manifest)
    state._atomic_write_json(DIST_DIR / MANIFEST_NAME, manifest)
    return {
        "batch_id": batch_id,
        "dist_dir": str(DIST_DIR),
        "asset_count": len(fingerprints),
        "manifest": str(state.root / MANIFEST_NAME),
    }


def load_manifest(batch_id: str) -> dict[str, Any] | None:
    from app.pipeline.state import PipelineState

    candidate = PipelineState().root / MANIFEST_NAME
    return manifest_for_batch(candidate, batch_id)


def manifest_for_batch(candidate: Path, batch_id: str) -> dict[str, Any] | None:
    if not candidate.exists():
        return None
    import json

    manifest = json.loads(candidate.read_text(encoding="utf-8"))
    if manifest.get("batch_id") != batch_id:
        return None
    return manifest


def manifest_for_current_batch(batch_id: str) -> dict[str, Any] | None:
    """当前批次构建清单：状态目录或已部署 dist 里任一存在且批次匹配即可。"""
    state_manifest = load_manifest(batch_id)
    if state_manifest is not None:
        return state_manifest
    return manifest_for_batch(DIST_DIR / MANIFEST_NAME, batch_id)


def restore_dist_manifest(batch_id: str) -> dict[str, Any] | None:
    """把状态目录里的当前批次清单回填到 dist（幂等）。

    独立执行的 ``npm run build`` 会清空 dist；流水线在构建阶段已成功的情况下
    重跑时不再构建，用这里保证部署目录仍带当前批次清单。
    """
    from app.pipeline.state import PipelineState

    state_manifest = load_manifest(batch_id)
    if state_manifest is None or not DIST_DIR.exists():
        return None
    target = DIST_DIR / MANIFEST_NAME
    existing = manifest_for_batch(target, batch_id)
    if existing is None:
        PipelineState()._atomic_write_json(target, state_manifest)
    return state_manifest
