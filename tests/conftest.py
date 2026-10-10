"""共享 fixture：内存 SQLite + 开发登录 + Stub TextIn client。

不触碰远程 MySQL / 真实 TextIn。需要任务跑完的测试直接调用
OcrWorker._process（同步、自带应用上下文），不起后台线程。

注意：Werkzeug 测试客户端的 Cookie jar 会覆盖显式 Cookie 头，
因此每个用户用独立 client（jar 即身份），headers 只带 CSRF。
"""

from __future__ import annotations

import pytest

from app import create_app

PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000d4944415478da63fcffff3f030005fe02fea7358146"
    "0000000049454e44ae426082"
)


class StubTextInClient:
    """返回 TextIn 真实形状的响应（cells 用 row/col/text）。"""

    endpoint = "stub"
    extract_endpoint = "stub"

    def __init__(self, payload: dict | None = None) -> None:
        self.payload = payload or {
            "code": 200,
            "pages": [{"page_number": 1}],
            "metadata": {"page_count": 1},
            "elements": [
                {
                    "element_id": "e1",
                    "type": "text",
                    "page_number": 1,
                    "text": "Invoice 123",
                    "coordinates": [0, 0, 100, 0, 100, 20, 0, 20],
                },
                {
                    "element_id": "t1",
                    "type": "Table",
                    "page_number": 1,
                    "table_structure": {
                        "cells": [
                            {"row": 0, "col": 0, "row_span": 1, "col_span": 1, "text": "00123"},
                            {"row": 0, "col": 1, "row_span": 1, "col_span": 1, "text": "apple"},
                            {"row": 1, "col": 0, "row_span": 1, "col_span": 1, "text": "date"},
                            {"row": 1, "col": 1, "row_span": 1, "col_span": 1, "text": "2026-10-10"},
                        ]
                    },
                },
            ],
            "markdown": "Invoice 123",
        }
        self.calls: list[dict] = []

    def extract_file(self, filename, content, content_type, params=None):
        self.calls.append({"kind": "parse", "params": params})
        return dict(self.payload)

    def extract_with_schema(self, **kwargs):
        self.calls.append({"kind": "extract", "kwargs": kwargs})
        return {
            "code": 200,
            "result": {
                "extracted_schema": {"Invoice No": {"value": "00123"}},
            },
        }

    def close(self) -> None:
        pass


@pytest.fixture
def app(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "AUTH_DEV_MODE": True,
            "LINE_CHANNEL_ID": "",
            "OCR_WORKER_ENABLED": False,
            "FILE_STORAGE_DIR": str(tmp_path / "files"),
            "EXPORT_STORAGE_DIR": str(tmp_path / "exports"),
            "OCR_STORAGE_DIR": str(tmp_path / "audit"),
            "TEXTIN_APP_ID": "stub",
            "TEXTIN_SECRET_CODE": "stub",
        }
    )
    app.extensions["textin_client"] = StubTextInClient()
    yield app


@pytest.fixture
def client(app):
    """匿名客户端（空 jar），用于未登录门禁断言。"""
    return app.test_client()


@pytest.fixture
def auth(app):
    """开发登录 tester，返回 (client, headers, user)。

    client 的 Cookie jar 即会话身份；headers 只含 CSRF。
    """
    c = app.test_client()
    r = c.post("/api/v1/auth/line", json={"dev_id": "tester"})
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    return c, {"X-CSRF-Token": data["csrf_token"]}, data["user"]


@pytest.fixture
def ready_file(app, auth):
    """上传一个 READY 状态的 PNG，返回 (client, headers, file dict)。"""
    c, h, _ = auth
    r = c.post(
        "/api/v1/uploads",
        json={"file_name": "inv.png", "size_bytes": len(PNG_1PX)},
        headers=h,
    )
    assert r.status_code == 201, r.get_json()
    f = r.get_json()["data"]["file"]
    r = c.put(
        f"/api/v1/uploads/{f['id']}/content",
        data=PNG_1PX,
        headers={"Content-Type": "image/png", **h},
    )
    assert r.status_code == 200, r.get_json()
    r = c.post(f"/api/v1/uploads/{f['id']}/complete", headers=h)
    assert r.status_code == 200, r.get_json()
    return c, h, r.get_json()["data"]["file"]


@pytest.fixture
def succeeded_job(app, auth, ready_file):
    """同步跑完 worker 的 SUCCEEDED 任务，返回 (job_id, client, headers)。"""
    c, h, f = ready_file
    r = c.post(
        "/api/v1/ocr/jobs",
        json={"file_id": f["id"], "params": {"table": True}},
        headers=h,
    )
    assert r.status_code == 201, r.get_json()
    job_id = r.get_json()["data"]["id"]

    from worker import OcrWorker

    OcrWorker(app)._process(job_id)
    return job_id, c, h
