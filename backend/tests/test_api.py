"""HTTP 层测试：健康检查、概览批次、台账批次戳、偏离清单一致性。"""
from __future__ import annotations


def _release(pipeline):
    pipeline.run()
    # store 与 pipeline 共用状态目录，重新装载到新发布
    from app.store import store

    store.bootstrap()


def test_health_reflects_readiness(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["listening"] is True
    assert resp.json()["ready"] is False
    assert resp.json()["batch_id"] is None


def test_overview_after_release(client, pipeline):
    _release(pipeline)
    resp = client.get("/api/overview")
    body = resp.json()
    assert body["batch_id"] == pipeline.batch_id
    assert body["reconciliation"]["matched"] is True
    cards = {c["label"]: c["value"] for c in body["cards"]}
    assert cards["跨日补录"] == 18
    assert len(body["modules"]) == 18


def test_ledger_list_has_same_batch(client, pipeline):
    """模块台账列表信封上的批次号必须与发布批次一致。"""
    _release(pipeline)
    resp = client.get("/api/borehole")
    body = resp.json()
    assert body["batch_id"] == pipeline.batch_id
    assert body["total"] == 4  # 3 条种子 + 1 条补录
    batch_only = client.get(f"/api/borehole?batch_id={pipeline.batch_id}").json()
    assert batch_only["total"] == 1
    assert batch_only["items"][0]["late_entry"] is True


def test_export_has_batch(client, pipeline):
    _release(pipeline)
    body = client.get("/api/borehole/export").json()
    assert body["batch_id"] == pipeline.batch_id
    assert body["module"] == "borehole"


def test_deviations_endpoint(client, pipeline):
    _release(pipeline)
    body = client.get("/api/pipeline/deviations").json()
    assert body["batch_id"] == pipeline.batch_id
    assert body["total"] == 18
    assert all(item["batch_id"] == pipeline.batch_id for item in body["items"])
    assert any(item["kind"] == "跨日补录" for item in body["items"])


def test_ledgers_endpoint_consistent(client, pipeline):
    _release(pipeline)
    body = client.get("/api/pipeline/ledgers").json()
    assert len(body["items"]) == 18
    for ledger in body["items"]:
        assert ledger["batch_id"] == pipeline.batch_id
        assert ledger["batch_consistent"] is True


def test_pipeline_status_endpoint(client, pipeline):
    _release(pipeline)
    body = client.get("/api/pipeline/status").json()
    assert body["release"]["batch_id"] == pipeline.batch_id
    assert body["timezone"] == "Asia/Shanghai"


def test_route_ordering_export_before_id(client, pipeline):
    """/export 必须在 /{entry_id} 之前注册，否则会被当成 int 报 422。"""
    _release(pipeline)
    resp = client.get("/api/core/export")
    assert resp.status_code == 200
    assert resp.json()["module"] == "core"


def test_actions_still_work(client, pipeline):
    """状态流转行为不被批次改造破坏。"""
    _release(pipeline)
    resp = client.post("/api/borehole/1/actions", json={"values": {"action": "开始钻进"}})
    assert resp.json()["ok"] is True
    assert resp.json()["entry"]["status"] == "钻进中"
