"""数据仓库：从流水线发布的台账装载，内存中提供筛选与状态流转。

启动时优先读取 DATA_DIR 下由流水线发布的 ledgers.json；流水线尚未跑过时
回退到内置种子（v1 示例），保证克隆下来直接起服务也有数据。
概览统计以当前批次台账为准，汇总页的核对结论从 summary.json 读取。
"""
from __future__ import annotations

import copy
from typing import Any

from app.pipeline import state as state_mod
from app.pipeline.constants import MODULES, MODULE_ORDER
from app.seed import SEED_ROWS


def _flatten_ledger(payload: Any) -> dict[str, list[dict[str, Any]]]:
    if isinstance(payload, dict) and "tables" in payload:
        return {name: list(rows) for name, rows in payload["tables"].items()}
    if isinstance(payload, dict) and payload:
        return {name: list(rows) for name, rows in payload.items() if isinstance(rows, list)}
    return {}


class Store:
    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {}
        self.reload()

    def reload(self) -> None:
        """从最近发布的台账重新装载（流水线发布/回滚后调用）。"""
        ledgers = _flatten_ledger(state_mod.read_json(state_mod.LEDGER_FILE, {}))
        if ledgers:
            self._tables = ledgers
            return
        # 尚未执行流水线：回退内置 v1 示例
        self._tables = {name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()}

    def module_names(self) -> list[str]:
        return sorted(self._tables)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def current_batch_id(self) -> str | None:
        return state_mod.load_state().get("current_batch_id")

    def current_rows(self, module: str) -> list[dict[str, Any]]:
        """当前批次行；没有发布批次时退化为全量（兼容内置种子）。"""
        batch_id = self.current_batch_id()
        rows = self.rows(module)
        if not batch_id:
            return rows
        return [row for row in rows if row.get("_batch_id") == batch_id]

    def overview(self) -> dict[str, object]:
        """运营概览：统计口径与发布批次一致，并带上核对结论。

        优先返回流水线重算并落盘的汇总统计；没有时按当前内存台账即时重算，
        保证动作流转后看板与台账不脱节。
        """
        batch_id = self.current_batch_id()
        summary = state_mod.read_json(state_mod.SUMMARY_FILE, {})
        if batch_id and summary.get("batch_id") == batch_id:
            cards = list(summary.get("cards", []))
            modules = [dict(item) for item in summary.get("modules", [])]
            return {
                "cards": cards,
                "modules": modules,
                "batch_id": batch_id,
                "reconciliation_id": summary.get("reconciliation_id"),
                "reconciliation": summary.get("reconciliation"),
            }

        modules: list[dict[str, object]] = []
        for name in MODULE_ORDER:
            rows = self.current_rows(name)
            modules.append({
                "name": name,
                "label": MODULES[name].label,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        result: dict[str, object] = {"cards": cards, "modules": modules}
        if batch_id:
            result["batch_id"] = batch_id
        return result

    def deviations(self) -> dict[str, Any]:
        return state_mod.read_json(state_mod.DEVIATION_FILE,
                                   {"batch_id": None, "items": [], "total": 0})

    def ledger(self, module: str) -> dict[str, Any]:
        ledger_file = state_mod.read_json(state_mod.LEDGER_FILE, {})
        stamps = ledger_file.get("stamps", {}) if isinstance(ledger_file, dict) else {}
        return {
            "module": module,
            "total": len(self.rows(module)),
            "items": copy.deepcopy(self.rows(module)),
            "stamp": stamps.get(module),
        }


store = Store()
