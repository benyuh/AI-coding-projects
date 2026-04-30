"""
tests/unit/test_service_smoke.py — v3.1.5 服务层冒烟测试

覆盖：
- /health 端点返回 200 + 正确版本号
- POST /render 返回 202 + job_id（不调真实 Pipeline）
- GET /status/{job_id} 返回已创建的任务状态
- GET /status/nonexistent 返回 404
- GET /download/{job_id} 未完成时返回 409
- InMemoryJobStore 线程安全创建/更新/查询
- JobStore.update 不可变模式（model_copy）
- schemas: RenderRequest 最短文本限制（<100字 ValidationError）
- schemas: JobStatus 枚举值完整性
- /render + mock Pipeline → 任务状态转为 done
"""
from __future__ import annotations

import sys
import os
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")

import pytest
from fastapi.testclient import TestClient

from service.app import app
from service.job_store import InMemoryJobStore, get_job_store
from service.schemas import JobStatus, RenderRequest


# ── /health ────────────────────────────────────────────────────────────────────

def test_health_returns_200():
    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["version"] == "3.1.5"


# ── POST /render ───────────────────────────────────────────────────────────────

def test_render_returns_202_with_job_id():
    with TestClient(app) as client:
        resp = client.post("/render", json={
            "text": "测试长文" * 30,   # 120字，满足 min_length=100
            "use_gates": False,
        })
    assert resp.status_code == 202
    data = resp.json()
    assert "job_id" in data
    assert len(data["job_id"]) > 0
    assert data["status"] == "pending"


def test_render_text_too_short_returns_422():
    with TestClient(app) as client:
        resp = client.post("/render", json={"text": "短文"})
    assert resp.status_code == 422, "文本不足100字应返回 422"


# ── GET /status ────────────────────────────────────────────────────────────────

def test_status_existing_job():
    with TestClient(app) as client:
        # 先创建任务
        resp = client.post("/render", json={"text": "x" * 120, "use_gates": False})
        job_id = resp.json()["job_id"]

        # 查询状态（任务可能是 pending 或 running）
        status_resp = client.get(f"/status/{job_id}")
    assert status_resp.status_code == 200
    data = status_resp.json()
    assert data["job_id"] == job_id
    assert data["status"] in ("pending", "running", "done", "failed")


def test_status_nonexistent_returns_404():
    with TestClient(app) as client:
        resp = client.get("/status/nonexistent-job-id-xyz")
    assert resp.status_code == 404


# ── GET /download ──────────────────────────────────────────────────────────────

def test_download_nonexistent_returns_404():
    with TestClient(app) as client:
        resp = client.get("/download/fake-job-id")
    assert resp.status_code == 404


def test_download_pending_job_returns_409():
    """pending 状态的任务不能下载。"""
    store = get_job_store()
    job_id = store.create()
    # 保持 pending 状态

    with TestClient(app) as client:
        resp = client.get(f"/download/{job_id}")
    assert resp.status_code == 409


# ── InMemoryJobStore 单元测试 ──────────────────────────────────────────────────

def test_job_store_create_and_get():
    store = InMemoryJobStore()
    jid = store.create()
    assert store.exists(jid)
    result = store.get(jid)
    assert result is not None
    assert result.job_id == jid
    assert result.status == JobStatus.PENDING


def test_job_store_update():
    store = InMemoryJobStore()
    jid = store.create()
    store.update(jid, status=JobStatus.RUNNING)
    assert store.get(jid).status == JobStatus.RUNNING
    store.update(jid, status=JobStatus.DONE, output_path="/tmp/test.png", file_size_kb=42)
    result = store.get(jid)
    assert result.status == JobStatus.DONE
    assert result.output_path == "/tmp/test.png"
    assert result.file_size_kb == 42


def test_job_store_update_nonexistent_no_crash():
    """更新不存在的 job_id 不应崩溃。"""
    store = InMemoryJobStore()
    store.update("not-a-real-id", status=JobStatus.FAILED)  # 不应抛异常


def test_job_store_immutable_model_copy():
    """update 使用 model_copy，原始对象不被修改。"""
    store = InMemoryJobStore()
    jid = store.create()
    original = store.get(jid)
    store.update(jid, status=JobStatus.RUNNING)
    updated = store.get(jid)
    # 原始引用状态不应被改变（model_copy 返回新对象）
    assert original.status == JobStatus.PENDING
    assert updated.status == JobStatus.RUNNING


def test_job_store_thread_safety():
    """100 个线程并发创建任务，每个 job_id 唯一。"""
    store = InMemoryJobStore()
    ids = []
    lock = threading.Lock()

    def create_job():
        jid = store.create()
        with lock:
            ids.append(jid)

    threads = [threading.Thread(target=create_job) for _ in range(100)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(ids) == 100
    assert len(set(ids)) == 100, "所有 job_id 应唯一"


# ── schemas 校验 ───────────────────────────────────────────────────────────────

def test_render_request_valid():
    req = RenderRequest(text="a" * 100)
    assert req.use_gates is True
    assert req.save_blueprint is False


def test_render_request_invalid_min_length():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        RenderRequest(text="短")


def test_job_status_enum_completeness():
    values = {s.value for s in JobStatus}
    assert "pending" in values
    assert "running" in values
    assert "done" in values
    assert "failed" in values


# ── mock Pipeline 集成测试 ─────────────────────────────────────────────────────

def test_render_with_mock_pipeline(monkeypatch, tmp_path):
    """mock run_pipeline，验证任务状态最终变为 done。"""
    fake_png = tmp_path / "fake.png"
    fake_png.write_bytes(b"PNG" * 100)

    def mock_run_pipeline(text, output_path, save_blueprint=False, use_gates=True, source_bundle=None, output_format="image"):
        # 写入假 PNG
        import shutil
        shutil.copy(str(fake_png), output_path)
        return output_path

    monkeypatch.setattr("main.run_pipeline", mock_run_pipeline)

    with TestClient(app) as client:
        resp = client.post("/render", json={"text": "y" * 120, "use_gates": False})
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]

        # 等待线程池完成（最多 5 秒）
        deadline = time.time() + 5
        while time.time() < deadline:
            sr = client.get(f"/status/{job_id}")
            if sr.json()["status"] in ("done", "failed"):
                break
            time.sleep(0.05)

        final = client.get(f"/status/{job_id}").json()

    assert final["status"] == "done", f"期望 done，实际: {final}"
    assert final["output_path"] is not None
    assert final["file_size_kb"] is not None


if __name__ == "__main__":
    test_health_returns_200()
    test_render_returns_202_with_job_id()
    test_render_text_too_short_returns_422()
    test_status_existing_job()
    test_status_nonexistent_returns_404()
    test_download_nonexistent_returns_404()
    test_download_pending_job_returns_409()
    test_job_store_create_and_get()
    test_job_store_update()
    test_job_store_update_nonexistent_no_crash()
    test_job_store_immutable_model_copy()
    test_job_store_thread_safety()
    test_render_request_valid()
    test_render_request_invalid_min_length()
    test_job_status_enum_completeness()
    print("\n所有服务层冒烟测试通过 ✅")
