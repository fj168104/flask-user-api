"""TextIn Flask Blueprint 路由集成测试。

覆盖：
- POST /extract (file/url/字段组合/校验/错误/未配置)
- GET  /extractions (空列表、详情联动、未知 ID)
- GET  /extractions/{id}/file (file/url/未知 ID)
- 路由注册表 (4 条新路由)
- 未授权：所有路由均不检查 Bearer token
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from flask import Flask

from textin.client import TextInClient


# ---------- route registration ----------


def test_route_map_registered(textin_app: Flask) -> None:
    rules = sorted(str(r) for r in textin_app.url_map.iter_rules())
    assert "/api/v1/ocr/extract" in rules
    assert "/api/v1/ocr/extractions" in rules
    assert "/api/v1/ocr/extractions/<extraction_id>" in rules
    assert "/api/v1/ocr/extractions/<extraction_id>/file" in rules
    assert "/api/v1/ocr/extractions/<extraction_id>/refine" in rules


def test_no_auth_required_on_any_route(client) -> None:
    """All new routes are unauthenticated per migration decision."""
    for path in (
        "/api/v1/ocr/extractions",
        "/api/v1/ocr/extractions/11111111-1111-4111-8111-111111111111",
        "/api/v1/ocr/extractions/11111111-1111-4111-8111-111111111111/file",
    ):
        resp = client.get(path)
        # All return 200 or 404 (not 401), confirming no token required.
        assert resp.status_code != 401, (path, resp.json())


# ---------- POST /extract ----------


def test_extract_url_returns_summary_and_writes_json(client, tmp_path: Path) -> None:
    resp = client.post(
        "/api/v1/ocr/extract",
        data={
            "source_type": "url",
            "url": "https://example.com/sample.jpg",
            "parse_mode": "layout",
            "language": "auto",
            "table": "true",
        },
    )
    assert resp.status_code == 200, resp.data
    body = resp.get_json()
    assert body["code"] == 200
    assert body["msg"] == "提取成功"
    data = body["data"]
    assert data["source_type"] == "url"
    assert data["source_ref"] == "https://example.com/sample.jpg"
    assert data["summary"]["page_count"] == 2
    assert data["summary"]["item_count"] == 3

    files = list((tmp_path / "extractions").glob("*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["extraction_id"] == data["extraction_id"]


def test_extract_file_multipart_success(client, tmp_path: Path) -> None:
    resp = client.post(
        "/api/v1/ocr/extract",
        data={
            "source_type": "file",
            "file": (open(__file__, "rb"), "hello.png"),
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200, resp.data
    body = resp.get_json()
    assert body["data"]["source_type"] == "file"
    assert body["data"]["source_ref"] == "hello.png"
    assert body["data"]["summary"]["page_count"] == 2

    # uploaded file persisted
    eid = body["data"]["extraction_id"]
    saved = list((tmp_path / "uploads").glob(f"{eid}.*"))
    assert len(saved) == 1


def test_extract_file_without_file_returns_422(client) -> None:
    resp = client.post("/api/v1/ocr/extract", data={"source_type": "file"})
    assert resp.status_code == 422
    body = resp.get_json()
    assert body["code"] == 422
    assert "file" in body["msg"]


def test_extract_url_without_url_returns_422(client) -> None:
    resp = client.post("/api/v1/ocr/extract", data={"source_type": "url"})
    assert resp.status_code == 422
    body = resp.get_json()
    assert body["code"] == 422


def test_extract_invalid_source_type_returns_422(client) -> None:
    resp = client.post("/api/v1/ocr/extract", data={"source_type": "bogus"})
    assert resp.status_code == 422


def test_extract_empty_file_returns_400(client) -> None:
    resp = client.post(
        "/api/v1/ocr/extract",
        data={"source_type": "file", "file": (b"", "empty.png")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["code"] == 400


def test_extract_bad_bool_returns_422(client) -> None:
    resp = client.post(
        "/api/v1/ocr/extract",
        data={"source_type": "url", "url": "https://x.example/y.jpg", "table": "maybe"},
    )
    assert resp.status_code == 422


def test_extract_bad_int_returns_422(client) -> None:
    resp = client.post(
        "/api/v1/ocr/extract",
        data={"source_type": "url", "url": "https://x.example/y.jpg", "page": "abc"},
    )
    assert resp.status_code == 422


def test_extract_textin_business_error_returns_502(
    textin_app: Flask, mock_textin_payload: dict
) -> None:
    """When _client returns a TextIn business error, route returns 502."""
    # Replace the mock with one that returns a non-200 code.
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 40101, "message": "invalid app id"})

    textin_app.extensions["textin_client"] = TextInClient(
        textin_app.extensions["textin_settings"],
        transport=httpx.MockTransport(handler),
    )
    c = textin_app.test_client()
    resp = c.post(
        "/api/v1/ocr/extract",
        data={"source_type": "url", "url": "https://x.example/y.jpg"},
    )
    assert resp.status_code == 502
    body = resp.get_json()
    assert body["code"] == 502
    assert "40101" in body["msg"]


def test_extract_503_when_no_client(textin_app: Flask) -> None:
    """Missing credentials → client is None → POST returns 503; list routes still work."""
    textin_app.extensions["textin_client"] = None
    c = textin_app.test_client()
    resp = c.post(
        "/api/v1/ocr/extract",
        data={"source_type": "url", "url": "https://x.example/y.jpg"},
    )
    assert resp.status_code == 503
    body = resp.get_json()
    assert body["code"] == 503
    assert "TextIn" in body["msg"]

    # GET endpoints still work — they don't need the client
    listing = c.get("/api/v1/ocr/extractions")
    assert listing.status_code == 200
    assert listing.get_json()["data"] == []


def test_extract_uses_schema_when_field_names(textin_app: Flask) -> None:
    schema_called = {"count": 0}
    parse_called = {"count": 0}
    mock_textin_payload = {
        "code": 200,
        "message": "ok",
        "elements": [
            {
                "type": "Table",
                "page_number": 1,
                "table_structure": {
                    "cells": [
                        {"row": 0, "col": 0, "text": "K"},
                        {"row": 0, "col": 1, "text": "V"},
                        {"row": 1, "col": 0, "text": "发票号"},
                        {"row": 1, "col": 1, "text": "HEUR-1"},
                    ]
                },
            }
        ],
        "markdown": "金额: 1.00",
        "metadata": {"page_count": 1},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = str(request.url.path)
        if path.endswith("/extract/sync"):
            schema_called["count"] += 1
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": {
                        "result": {
                            "extracted_schema": {
                                "发票号": "SCHEMA-1",
                                "金额": "99.00",
                            }
                        }
                    },
                },
            )
        parse_called["count"] += 1
        return httpx.Response(200, json=mock_textin_payload)

    textin_app.extensions["textin_client"] = TextInClient(
        textin_app.extensions["textin_settings"],
        transport=httpx.MockTransport(handler),
    )
    c = textin_app.test_client()
    resp = c.post(
        "/api/v1/ocr/extract",
        data={
            "source_type": "url",
            "url": "https://x.example/y.jpg",
            "field_names": ["发票号", "金额"],
        },
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert schema_called["count"] == 0  # URL never calls schema
    assert {f["source"] for f in body["data"]["fields"]} == {"table", "markdown"}


def test_extract_url_does_not_save_upload(client, tmp_path: Path) -> None:
    resp = client.post(
        "/api/v1/ocr/extract",
        data={"source_type": "url", "url": "https://x.example/y.jpg"},
    )
    assert resp.status_code == 200
    assert list((tmp_path / "uploads").iterdir()) == []


# ---------- GET /extractions ----------


def test_list_extractions_empty(client) -> None:
    resp = client.get("/api/v1/ocr/extractions")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body == {"code": 200, "msg": "ok", "data": []}


def test_list_then_get_extraction(client) -> None:
    r1 = client.post(
        "/api/v1/ocr/extract",
        data={"source_type": "url", "url": "https://a.example/x.jpg"},
    )
    assert r1.status_code == 200
    r2 = client.post(
        "/api/v1/ocr/extract",
        data={"source_type": "url", "url": "https://b.example/y.jpg"},
    )
    assert r2.status_code == 200

    listing = client.get("/api/v1/ocr/extractions").get_json()
    assert listing["code"] == 200
    assert len(listing["data"]) == 2
    assert {item["source_ref"] for item in listing["data"]} == {
        "https://a.example/x.jpg",
        "https://b.example/y.jpg",
    }

    first_id = listing["data"][0]["extraction_id"]
    detail = client.get(f"/api/v1/ocr/extractions/{first_id}")
    assert detail.status_code == 200
    detail_body = detail.get_json()
    assert detail_body["data"]["extraction_id"] == first_id
    assert "textin_response" in detail_body["data"]


def test_get_unknown_extraction_returns_404(client) -> None:
    resp = client.get("/api/v1/ocr/extractions/11111111-1111-4111-8111-111111111111")
    assert resp.status_code == 404
    body = resp.get_json()
    assert body["code"] == 404


def test_get_invalid_id_returns_404(client) -> None:
    """UUID guard: non-UUID paths are rejected as 404, never reach filesystem."""
    resp = client.get("/api/v1/ocr/extractions/not-a-uuid")
    assert resp.status_code == 404
    assert resp.get_json()["code"] == 404


# ---------- GET /extractions/<id>/file ----------


def test_get_file_returns_bytes(client) -> None:
    from io import BytesIO

    r = client.post(
        "/api/v1/ocr/extract",
        data={"source_type": "file", "file": (BytesIO(b"%PDF-1.4\nfake"), "a.pdf")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 200, r.data
    eid = r.get_json()["data"]["extraction_id"]

    got = client.get(f"/api/v1/ocr/extractions/{eid}/file")
    assert got.status_code == 200
    assert got.data == b"%PDF-1.4\nfake"
    assert got.headers["content-type"].startswith("application/pdf")
    assert 'filename="' in got.headers.get("content-disposition", "")


def test_get_file_url_source_returns_404(client) -> None:
    r = client.post(
        "/api/v1/ocr/extract",
        data={"source_type": "url", "url": "https://x.example/y.jpg"},
    )
    eid = r.get_json()["data"]["extraction_id"]

    got = client.get(f"/api/v1/ocr/extractions/{eid}/file")
    assert got.status_code == 404
    body = got.get_json()
    assert body["code"] == 404


def test_get_file_unknown_id_returns_404(client) -> None:
    got = client.get("/api/v1/ocr/extractions/nonexistent-id/file")
    assert got.status_code == 404


def test_get_file_invalid_id_returns_404(client) -> None:
    got = client.get("/api/v1/ocr/extractions/../../etc/passwd/file")
    assert got.status_code == 404


# ---------- POST /extractions/<id>/refine ----------


def _make_file_record(client) -> str:
    """Helper: POST /extract with a real file → return extraction_id."""
    from io import BytesIO

    r = client.post(
        "/api/v1/ocr/extract",
        data={
            "source_type": "file",
            "file": (BytesIO(b"%PDF-1.4\nfake"), "a.pdf"),
        },
        content_type="multipart/form-data",
    )
    assert r.status_code == 200, r.data
    return r.get_json()["data"]["extraction_id"]


def test_refine_happy_path_replaces_fields(textin_app: Flask, client) -> None:
    """happy path: 旧字段命中同名 schema 会被替换，未命中的保留，新增追加；field_source 变 mixed。"""
    schema_called = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = str(request.url.path)
        if path.endswith("/extract/sync"):
            schema_called["count"] += 1
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": {
                        "result": {
                            "extracted_schema": {
                                "发票号": "SCHEMA-NEW",  # 替换旧启发式 "INV-1"
                                "日期": "2026-10-10",     # 新字段
                            }
                        }
                    },
                },
            )
        # parse/sync：返回的 markdown 里应包含启发式字段
        return httpx.Response(
            200,
            json={
                "code": 200,
                "elements": [],
                "metadata": {"page_count": 1},
                "markdown": "# Invoice\n发票号: INV-1\n金额: 99.00",
            },
        )

    textin_app.extensions["textin_client"] = TextInClient(
        textin_app.extensions["textin_settings"],
        transport=httpx.MockTransport(handler),
    )
    c = textin_app.test_client()

    eid = _make_file_record(c)
    # 先验证旧字段是启发式
    before = c.get(f"/api/v1/ocr/extractions/{eid}").get_json()["data"]
    names_before = {(f["name"], f["source"]) for f in before["fields"]}
    assert ("发票号", "markdown") in names_before

    # refine
    resp = c.post(
        f"/api/v1/ocr/extractions/{eid}/refine",
        json={"field_names": ["发票号", "日期"]},
    )
    assert resp.status_code == 200, resp.data
    body = resp.get_json()
    assert body["code"] == 200
    record = body["data"]
    fields = {(f["name"], f["source"]) for f in record["fields"]}
    # 替换: 发票号 → schema
    assert ("发票号", "schema") in fields
    # 新增: 字段 date
    assert ("日期", "schema") in fields
    # 启发式 金额 保留
    assert ("金额", "markdown") in fields
    assert schema_called["count"] == 1


def test_refine_empty_field_names_returns_422(client) -> None:
    eid = _make_file_record(client)
    resp = client.post(f"/api/v1/ocr/extractions/{eid}/refine", json={"field_names": []})
    assert resp.status_code == 422
    assert resp.get_json()["code"] == 422


def test_refine_missing_field_names_returns_422(client) -> None:
    eid = _make_file_record(client)
    resp = client.post(f"/api/v1/ocr/extractions/{eid}/refine", json={})
    assert resp.status_code == 422


def test_refine_too_many_field_names_returns_422(client) -> None:
    eid = _make_file_record(client)
    resp = client.post(
        f"/api/v1/ocr/extractions/{eid}/refine",
        json={"field_names": [f"f{i}" for i in range(51)]},
    )
    assert resp.status_code == 422
    assert "上限" in resp.get_json()["msg"]


def test_refine_url_source_returns_422(client) -> None:
    """URL 抽取结果不允许 refine → 422 with specific msg。"""
    r = client.post(
        "/api/v1/ocr/extract",
        data={"source_type": "url", "url": "https://x.example/y.jpg"},
    )
    eid = r.get_json()["data"]["extraction_id"]

    resp = client.post(
        f"/api/v1/ocr/extractions/{eid}/refine",
        json={"field_names": ["发票号"]},
    )
    assert resp.status_code == 422
    assert "URL" in resp.get_json()["msg"]


def test_refine_unknown_extraction_returns_404(client) -> None:
    resp = client.post(
        "/api/v1/ocr/extractions/11111111-1111-4111-8111-111111111111/refine",
        json={"field_names": ["发票号"]},
    )
    assert resp.status_code == 404


def test_refine_invalid_id_returns_404(client) -> None:
    resp = client.post(
        "/api/v1/ocr/extractions/not-a-uuid/refine",
        json={"field_names": ["发票号"]},
    )
    assert resp.status_code == 404


def test_refine_textin_business_error_returns_502(textin_app: Flask, client) -> None:
    """schema 接口返回业务错误时，路由映射 502。"""
    from io import BytesIO

    def make_record() -> str:
        # 先用默认 mock 跑一次 extract 建档
        r = client.post(
            "/api/v1/ocr/extract",
            data={
                "source_type": "file",
                "file": (BytesIO(b"%PDF-1.4\nfake"), "a.pdf"),
            },
            content_type="multipart/form-data",
        )
        assert r.status_code == 200, r.data
        return r.get_json()["data"]["extraction_id"]

    eid = make_record()

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 40101, "message": "invalid app id"})

    textin_app.extensions["textin_client"] = TextInClient(
        textin_app.extensions["textin_settings"],
        transport=httpx.MockTransport(handler),
    )
    c = textin_app.test_client()

    resp = c.post(
        f"/api/v1/ocr/extractions/{eid}/refine",
        json={"field_names": ["发票号"]},
    )
    assert resp.status_code == 502
    assert "40101" in resp.get_json()["msg"]


def test_refine_503_when_no_client(textin_app: Flask, client) -> None:
    eid = _make_file_record(client)

    textin_app.extensions["textin_client"] = None
    c = textin_app.test_client()
    resp = c.post(
        f"/api/v1/ocr/extractions/{eid}/refine",
        json={"field_names": ["发票号"]},
    )
    assert resp.status_code == 503


def test_refine_in_place_update_no_new_record(textin_app: Flask, client, tmp_path: Path) -> None:
    """refine 后 extraction_id 不变（原地更新），records 总数不变。"""
    eid = _make_file_record(client)

    # count before
    before = client.get("/api/v1/ocr/extractions").get_json()["data"]
    assert len(before) == 1

    def handler(request: httpx.Request) -> httpx.Response:
        path = str(request.url.path)
        if path.endswith("/extract/sync"):
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": {"result": {"extracted_schema": {"发票号": "NEW"}}},
                },
            )
        return httpx.Response(
            200,
            json={
                "code": 200,
                "elements": [],
                "metadata": {"page_count": 1},
                "markdown": "发票号: OLD",
            },
        )

    textin_app.extensions["textin_client"] = TextInClient(
        textin_app.extensions["textin_settings"],
        transport=httpx.MockTransport(handler),
    )
    c = textin_app.test_client()

    resp = c.post(
        f"/api/v1/ocr/extractions/{eid}/refine",
        json={"field_names": ["发票号"]},
    )
    assert resp.status_code == 200
    assert resp.get_json()["data"]["extraction_id"] == eid

    # count after — still 1
    after = client.get("/api/v1/ocr/extractions").get_json()["data"]
    assert len(after) == 1
    assert after[0]["extraction_id"] == eid