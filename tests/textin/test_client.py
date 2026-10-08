"""TextIn 客户端单元/集成测试：build_config / schema 构造 / parse / 端点形态。"""

from __future__ import annotations

import json

import httpx
import pytest

from textin.client import (
    TextInClient,
    build_config,
    build_extract_schema,
    parse_extracted_schema,
)
from textin.config import TextInSettings
from textin.errors import TextInAPIError


# ---------- build_extract_schema ----------


def test_build_schema_empty() -> None:
    assert build_extract_schema([]) == {"type": "object", "properties": {}}


def test_build_schema_all_string() -> None:
    out = build_extract_schema(["发票号", "金额", "Date"])
    assert out == {
        "type": "object",
        "properties": {
            "发票号": {"type": "string"},
            "金额": {"type": "string"},
            "Date": {"type": "string"},
        },
    }


def test_build_schema_filters_empty_names() -> None:
    out = build_extract_schema(["", "发票号", None, "金额"])
    assert out["properties"] == {
        "发票号": {"type": "string"},
        "金额": {"type": "string"},
    }


# ---------- parse_extracted_schema ----------


def test_parse_schema_simple_value() -> None:
    extracted = {"发票号": "INV-001", "金额": "100.00"}
    out = parse_extracted_schema(extracted)
    assert out == [
        {"page_number": 0, "name": "发票号", "value": "INV-001", "source": "schema"},
        {"page_number": 0, "name": "金额", "value": "100.00", "source": "schema"},
    ]


def test_parse_schema_citation_format() -> None:
    extracted = {
        "发票号": {
            "value": "INV-002",
            "bounding_regions": [{"page_number": 1, "x": 0, "y": 0}],
        }
    }
    out = parse_extracted_schema(extracted, page_number=1)
    assert out == [
        {"page_number": 1, "name": "发票号", "value": "INV-002", "source": "schema"},
    ]


def test_parse_schema_skips_empty_values() -> None:
    extracted = {"发票号": "", "金额": None, "Date": "2026-01-01"}
    out = parse_extracted_schema(extracted)
    assert len(out) == 1
    assert out[0]["name"] == "Date"


def test_parse_schema_none_input() -> None:
    assert parse_extracted_schema(None) == []


def test_parse_schema_value_with_int_coerced() -> None:
    out = parse_extracted_schema({"count": 42})
    assert out == [
        {"page_number": 0, "name": "count", "value": "42", "source": "schema"},
    ]


# ---------- build_config ----------


def test_build_config_minimal() -> None:
    out = json.loads(build_config({}))
    assert out == {"capabilities": {"include_table_structure": False, "crop_dewarp": False}}


def test_build_config_parse_mode_and_table() -> None:
    out = json.loads(build_config({"parse_mode": "layout", "table": True}))
    assert out["capabilities"]["include_table_structure"] is True
    assert out["config"]["engine_params"]["parse_mode"] == "layout"


def test_build_config_formula_levels() -> None:
    assert json.loads(build_config({"formula": True}))["config"]["engine_params"]["formula_level"] == 1
    assert json.loads(build_config({"formula": False}))["config"]["engine_params"]["formula_level"] == 0


def test_build_config_page_range() -> None:
    # page=0, page_count=2 → TextIn 1-based: 1-2
    out = json.loads(build_config({"page": 0, "page_count": 2}))
    assert out["scope"]["page_range"] == "1-2"


def test_build_config_page_only() -> None:
    # page=2 → 3-3
    out = json.loads(build_config({"page": 2}))
    assert out["scope"]["page_range"] == "3-3"


def test_build_config_page_count_only() -> None:
    # page=0 + page_count=5 → 1-5
    out = json.loads(build_config({"page": 0, "page_count": 5}))
    assert out["scope"]["page_range"] == "1-5"


def test_build_config_rotate_flag() -> None:
    out = json.loads(build_config({"rotate": True}))
    assert out["capabilities"]["crop_dewarp"] is True


def test_build_config_none_input() -> None:
    assert json.loads(build_config(None)) == {
        "capabilities": {"include_table_structure": False, "crop_dewarp": False}
    }


# ---------- TextInClient ----------


@pytest.fixture
def settings() -> TextInSettings:
    return TextInSettings(
        textin_app_id="test_app_id",
        textin_secret_code="test_secret_code",
        textin_endpoint="https://api.textin.com/api/v1/xparse/parse/sync",
        textin_extract_endpoint="https://api.textin.com/api/xparse/extract/sync",
    )


def _make_client(settings: TextInSettings, handler) -> TextInClient:
    return TextInClient(settings, transport=httpx.MockTransport(handler))


def test_extract_file_hits_parse_sync(settings: TextInSettings) -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = str(request.url.path)
        captured["content_type"] = request.headers.get("content-type", "")
        return httpx.Response(
            200,
            json={"code": 200, "data": {"elements": [], "markdown": "hello"}},
        )

    client = _make_client(settings, handler)
    try:
        out = client.extract_file("a.png", b"\x89PNG", "image/png", {"parse_mode": "layout"})
    finally:
        client.close()

    assert captured["path"].endswith("/parse/sync")
    assert "multipart/form-data" in captured["content_type"]
    # data was promoted to top level
    assert out["elements"] == []
    assert out["markdown"] == "hello"


def test_extract_url_hits_parse_sync(settings: TextInSettings) -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = str(request.url.path)
        return httpx.Response(200, json={"code": 200, "data": {"elements": []}})

    client = _make_client(settings, handler)
    try:
        client.extract_url("https://example.com/x.jpg")
    finally:
        client.close()

    assert captured["path"].endswith("/parse/sync")


def test_extract_with_schema_hits_extract_sync(settings: TextInSettings) -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = str(request.url.path)
        captured["content_type"] = request.headers.get("content-type", "")
        return httpx.Response(
            200,
            json={
                "code": 200,
                "data": {
                    "result": {
                        "extracted_schema": {
                            "发票号": "X-1",
                            "金额": "9.99",
                        }
                    }
                },
            },
        )

    client = _make_client(settings, handler)
    try:
        out = client.extract_with_schema(
            filename="a.png",
            content=b"\x89PNG",
            content_type="image/png",
            schema_fields=["发票号", "金额"],
            parse_params={"parse_mode": "layout"},
        )
    finally:
        client.close()

    assert captured["path"].endswith("/extract/sync")
    assert "multipart/form-data" in captured["content_type"]
    result = out.get("result") or {}
    schema = result.get("extracted_schema") or {}
    assert schema == {"发票号": "X-1", "金额": "9.99"}


def test_business_error_raises(settings: TextInSettings) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 40101, "message": "invalid app id"})

    client = _make_client(settings, handler)
    try:
        with pytest.raises(TextInAPIError) as exc_info:
            client.extract_url("https://example.com/x.jpg")
    finally:
        client.close()
    assert exc_info.value.code == 40101
    assert "invalid app id" in str(exc_info.value)


def test_non_json_response_raises(settings: TextInSettings) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    client = _make_client(settings, handler)
    try:
        with pytest.raises(TextInAPIError) as exc_info:
            client.extract_url("https://example.com/x.jpg")
    finally:
        client.close()
    assert exc_info.value.code == -1
    assert "非 JSON" in str(exc_info.value)


def test_parse_promotes_data_to_top_level(settings: TextInSettings) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"code": 200, "message": "ok", "data": {"elements": [1, 2, 3], "foo": "bar"}},
        )

    client = _make_client(settings, handler)
    try:
        out = client.extract_url("https://example.com/x.jpg")
    finally:
        client.close()
    assert out["code"] == 200
    assert out["elements"] == [1, 2, 3]
    assert out["foo"] == "bar"
    assert out["message"] == "ok"


def test_parse_no_data_dict_returns_full_payload(settings: TextInSettings) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 200, "custom": "value"})

    client = _make_client(settings, handler)
    try:
        out = client.extract_url("https://example.com/x.jpg")
    finally:
        client.close()
    assert out == {"code": 200, "custom": "value"}