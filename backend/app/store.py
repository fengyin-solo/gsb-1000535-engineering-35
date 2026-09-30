"""数据仓库：对外只暴露内存表，但内容来自当前「已发布」批次快照。

启动装载顺序：

1. 流水线已发布（release 指针 + 对应批次快照就绪）-> 装载快照数据，
   服务随之 ready；这是生产/联调的正常路径；
2. 尚未发布任何批次 -> 退回内置 SEED_ROWS，ready=False，健康检查会显式
   报告“流水线尚未就绪”，避免把未成功阶段当作就绪。

仓库不写状态目录：发布/回滚是流水线的职责，服务重启后通过重新装载快照
来看到新版本。
"""
from __future__ import annotations

from typing import Any

from app.modules import REGISTRY
from app.pipeline.state import PipelineState
from app.seed import SEED_ROWS


class Store:
    def __init__(self) -> None:
        self.state = PipelineState()
        self._release: dict[str, Any] | None = None
        self._snapshot: dict[str, Any] | None = None
        self._tables: dict[str, list[dict[str, Any]]] = {
            name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
        }
        self._ready = False
        self.bootstrap()

    def bootstrap(self) -> None:
        """尝试从已发布快照装载；失败时静默回退种子，并把就绪标记保持为 False。"""
        try:
            release = self.state.load_release()
            if release:
                snapshot = self.state.load_snapshot(str(release.get("batch_id")))
                if snapshot and "tables" in snapshot:
                    self._release = release
                    self._snapshot = snapshot
                    self._tables = {
                        name: [dict(row) for row in rows]
                        for name, rows in snapshot["tables"].items()
                    }
                    self._ready = True
                    return
        except (OSError, ValueError):
            # 状态目录损坏/不可读：退回内置种子，但绝不谎报就绪
            pass
        self._release = None
        self._snapshot = None
        self._tables = {
            name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
        }
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready

    def release_info(self) -> dict[str, Any] | None:
        return dict(self._release) if self._release else None

    def module_names(self) -> list[str]:
        # 以台账注册表为准，避免种子缺失模块时概览口径漂移
        return [spec.name for spec in REGISTRY.values()]

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def overview(self) -> dict[str, object]:
        """运营概览：计数口径与流水线汇总一致，按当前发布批次统计。"""
        batch_id = self._release.get("batch_id") if self._release else None
        artifact = (self._snapshot or {}).get("artifact", {})
        # 直接采用发布快照里的汇总（发布时已由快照重算），并现场再算一遍交叉校验
        summary = artifact.get("summary") if isinstance(artifact, dict) else None
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            spec = REGISTRY[name]
            rows = self.rows(name)
            batch_rows = [row for row in rows if row.get("batch_id") == batch_id] if batch_id else rows
            modules.append({
                "name": name,
                "label": spec.label,
                "created": len(batch_rows),
                "pending": sum(1 for row in batch_rows if row.get("pending")),
                "abnormal": sum(1 for row in batch_rows if row.get("abnormal")),
                "backfill": sum(1 for row in batch_rows if row.get("late_entry")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "本批次新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
            {"label": "跨日补录", "value": sum(int(item["backfill"]) for item in modules)},
        ]
        reconciliation = summary.get("reconciliation") if summary else None
        return {
            "cards": cards,
            "modules": modules,
            "batch_id": batch_id,
            "timezone": (summary or {}).get("timezone"),
            "business_date": (summary or {}).get("business_date"),
            "reconciliation": reconciliation,
        }


store = Store()
