"""OCR 业务编排单元测试：safe_extension / persistence / run_extraction / list / get。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import httpx
import pytest

from textin.client import TextInClient
from textin.config import TextInSettings
from textin.errors import (
    ExtractionNotFound,
    RecordParseError,
    UploadedFileNotFound,
)
from textin.service import (
    EXTRACTION_ID_PATTERN,
    find_uploaded_file,
    get_extraction,
    is_valid_extraction_id,
    list_extractions,
    run_extraction,
    safe_extension,
    summarize,
)


@pytest.fixture
def storage_dirs(tmp_path: Path) -> tuple[Path, Path]:
    ext = tmp_path / "extractions"
    upl = tmp_path / "uploads"
    ext.mkdir(parents=True, exist_ok=True)
    upl.mkdir(parents=True, exist_ok=True)
    return ext, upl


@pytest.fixture
def settings(storage_dirs: tuple[Path, Path]) -> TextInSettings:
    ext, upl = storage_dirs
    return TextInSettings(
        textin_app_id="test_app_id",
        textin_secret_code="test_secret_code",
        ocr_storage_dir=ext,
        ocr_uploads_dir=upl,
    )


def _client_with_handler(settings: TextInSettings, handler) -> TextInClient:
    return TextInClient(settings, transport=httpx.MockTransport(handler))


# ---------- safe_extension ----------


def test_safe_extension_from_content_type() -> None:
    assert safe_extension("image/png", None) == ".png"
    # Python's mimetypes returns ".jpg" for "image/jpeg" — match stdlib behavior.
    assert safe_extension("image/jpeg", None) == ".jpg"


def test_safe_extension_from_filename_when_no_content_type() -> None:
    assert safe_extension(None, "doc.pdf") == ".pdf"


def test_safe_extension_content_type_priority() -> None:
    # content_type wins over filename if both present
    assert safe_extension("image/png", "evil.html") == ".png"


def test_safe_extension_rejects_html() -> None:
    assert safe_extension(None, "x.html") == ".bin"
    assert safe_extension(None, "x.htm") == ".bin"


def test_safe_extension_rejects_svg() -> None:
    assert safe_extension(None, "logo.svg") == ".bin"


def test_safe_extension_rejects_js() -> None:
    assert safe_extension(None, "evil.js") == ".bin"


def test_safe_extension_missing_falls_back_to_bin() -> None:
    assert safe_extension(None, None) == ".bin"
    assert safe_extension(None, "") == ".bin"


def test_safe_extension_no_dot_in_filename() -> None:
    # Mirror source: rpartition splits the whole name as the "ext" segment.
    assert safe_extension(None, "no-dot-name") == ".no-dot-name"


# ---------- is_valid_extraction_id ----------


def test_is_valid_extraction_id_accepts_uuid4() -> None:
    assert is_valid_extraction_id("12345678-1234-4234-8234-123456789abc")
    assert is_valid_extraction_id(str(UUID(int=0)))


def test_is_valid_extraction_id_rejects_non_uuid() -> None:
    assert not is_valid_extraction_id("not-a-uuid")
    assert not is_valid_extraction_id("../../etc/passwd")
    assert not is_valid_extraction_id("12345678-1234-1234-1234-123456789abz")  # non-hex
    assert not is_valid_extraction_id("")
    assert not is_valid_extraction_id("12345678-1234-1234-1234-123456789abcd")  # 13 chars in last group


# ---------- find_uploaded_file ----------


def test_find_uploaded_file_returns_matching_path(
    storage_dirs: tuple[Path, Path], settings: TextInSettings
) -> None:
    ext_dir, upl_dir = storage_dirs
    eid = "11111111-1111-4111-8111-111111111111"
    target = upl_dir / f"{eid}.pdf"
    target.write_bytes(b"x")
    assert find_uploaded_file(upl_dir, eid) == target


def test_find_uploaded_file_missing_raises(
    storage_dirs: tuple[Path, Path],
) -> None:
    _, upl_dir = storage_dirs
    with pytest.raises(UploadedFileNotFound):
        find_uploaded_file(upl_dir, "11111111-1111-4111-8111-111111111111")


def test_find_uploaded_file_invalid_id_raises(
    storage_dirs: tuple[Path, Path],
) -> None:
    _, upl_dir = storage_dirs
    with pytest.raises(UploadedFileNotFound):
        find_uploaded_file(upl_dir, "../escape")


# ---------- summarize ----------


def test_summarize_metadata_page_count() -> None:
    out = summarize(
        {
            "metadata": {"page_count": 7},
            "elements": [
                {"type": "text", "page_number": 1, "text": "a"},
                {"type": "text", "page_number": 1, "text": "b"},
                {"type": "text", "page_number": 1, "text": "c"},
            ],
        }
    )
    assert out == {
        "page_count": 7,
        "item_count": 3,
        "duration_ms": None,
        "field_count": 0,
    }


def test_summarize_falls_back_to_pages_length() -> None:
    out = summarize({"pages": [{}, {}, {}]})
    assert out["page_count"] == 3




def test_summarize_with_duration() -> None:
    out = summarize({"duration": 812.7, "metadata": {"page_count": 2}})
    assert out["duration_ms"] == 812


def test_summarize_with_field_count() -> None:
    fields = [{"page_number": 0, "name": "a", "value": "1", "source": "markdown"}]
    out = summarize({"metadata": {"page_count": 1}}, fields=fields)
    assert out["field_count"] == 1


# ---------- run_extraction ----------


PARSE_RESPONSE = {
    "code": 200,
    "data": {
        "elements": [
            {
                "type": "Table",
                "page_number": 1,
                "table_structure": {
                    "cells": [
                        {"row": 0, "col": 0, "text": "K"},
                        {"row": 0, "col": 1, "text": "V"},
                        {"row": 1, "col": 0, "text": "发票号"},
                        {"row": 1, "col": 1, "text": "INV-1"},
                    ]
                },
            }
        ],
        "markdown": "金额: 100.00",
        "metadata": {"page_count": 1},
    },
}


def test_run_extraction_file_saves_upload_and_record(
    settings: TextInSettings, storage_dirs: tuple[Path, Path]
) -> None:
    ext_dir, upl_dir = storage_dirs
    called = {"count": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        called["count"] += 1
        return httpx.Response(200, json=PARSE_RESPONSE)

    client = _client_with_handler(settings, handler)
    try:
        result = run_extraction(
            settings,
            client,
            "file",
            "invoice.pdf",
            file_bytes=b"%PDF-1.4\nfake",
            content_type="application/pdf",
        )
    finally:
        client.close()

    assert called["count"] == 1
    assert result["source_type"] == "file"
    assert result["source_ref"] == "invoice.pdf"
    assert len(result["fields"]) == 2
    assert {f["name"] for f in result["fields"]} == {"发票号", "金额"}

    eid = result["extraction_id"]
    assert is_valid_extraction_id(eid)
    assert (upl_dir / f"{eid}.pdf").read_bytes() == b"%PDF-1.4\nfake"
    saved = (ext_dir / f"{eid}.json").read_text(encoding="utf-8")
    payload = json.loads(saved)
    assert payload["extraction_id"] == eid
    assert payload["source_type"] == "file"
    assert payload["field_source"] == "heuristic"


def test_run_extraction_url_does_not_save_upload(
    settings: TextInSettings, storage_dirs: tuple[Path, Path]
) -> None:
    ext_dir, upl_dir = storage_dirs

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=PARSE_RESPONSE)

    client = _client_with_handler(settings, handler)
    try:
        result = run_extraction(
            settings,
            client,
            "url",
            "https://example.com/inv.pdf",
        )
    finally:
        client.close()

    assert result["source_type"] == "url"
    assert list(upl_dir.iterdir()) == []
    # record still saved
    assert (ext_dir / f"{result['extraction_id']}.json").exists()


def test_run_extraction_uses_schema_when_field_names(
    settings: TextInSettings,
) -> None:
    parse_called = {"count": 0}
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
                            "extracted_schema": {"发票号": "SCHEMA-1", "金额": "99.00"}
                        }
                    },
                },
            )
        parse_called["count"] += 1
        return httpx.Response(200, json=PARSE_RESPONSE)

    client = _client_with_handler(settings, handler)
    try:
        result = run_extraction(
            settings,
            client,
            "file",
            "x.png",
            file_bytes=b"\x89PNG",
            content_type="image/png",
            field_names=["发票号", "金额"],
        )
    finally:
        client.close()

    assert parse_called["count"] == 1
    assert schema_called["count"] == 1
    assert {f["source"] for f in result["fields"]} == {"schema"}


def test_run_extraction_falls_back_to_heuristic_on_schema_failure(
    settings: TextInSettings,
) -> None:
    parse_called = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        parse_called["count"] += 1
        path = str(request.url.path)
        if path.endswith("/extract/sync"):
            return httpx.Response(500, json={"code": 500, "message": "boom"})
        return httpx.Response(200, json=PARSE_RESPONSE)

    client = _client_with_handler(settings, handler)
    try:
        result = run_extraction(
            settings,
            client,
            "file",
            "x.png",
            file_bytes=b"\x89PNG",
            content_type="image/png",
            field_names=["发票号"],
        )
    finally:
        client.close()

    assert parse_called["count"] == 2  # parse + schema (failed)
    assert {f["source"] for f in result["fields"]} == {"table", "markdown"}


def test_run_extraction_url_with_field_names_skips_schema(
    settings: TextInSettings,
) -> None:
    parse_called = {"count": 0}
    schema_called = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = str(request.url.path)
        if path.endswith("/extract/sync"):
            schema_called["count"] += 1
            return httpx.Response(200, json={"code": 200, "data": {"result": {}}})
        parse_called["count"] += 1
        return httpx.Response(200, json=PARSE_RESPONSE)

    client = _client_with_handler(settings, handler)
    try:
        result = run_extraction(
            settings,
            client,
            "url",
            "https://example.com/inv.pdf",
            field_names=["发票号"],
        )
    finally:
        client.close()

    assert parse_called["count"] == 1
    assert schema_called["count"] == 0  # URL never calls schema
    assert {f["source"] for f in result["fields"]} == {"table", "markdown"}


def test_run_extraction_dangerous_filename_extension_replaced(
    settings: TextInSettings, storage_dirs: tuple[Path, Path]
) -> None:
    ext_dir, upl_dir = storage_dirs

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=PARSE_RESPONSE)

    client = _client_with_handler(settings, handler)
    try:
        result = run_extraction(
            settings,
            client,
            "file",
            "evil.html",
            file_bytes=b"<x>",
            content_type="text/html",
        )
    finally:
        client.close()

    eid = result["extraction_id"]
    saved_files = list(upl_dir.glob(f"{eid}.*"))
    assert len(saved_files) == 1
    assert saved_files[0].suffix == ".bin"


def test_run_extraction_atomic_write(
    settings: TextInSettings, storage_dirs: tuple[Path, Path]
) -> None:
    ext_dir, _ = storage_dirs

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=PARSE_RESPONSE)

    client = _client_with_handler(settings, handler)
    try:
        run_extraction(
            settings,
            client,
            "url",
            "https://example.com/inv.pdf",
        )
    finally:
        client.close()

    # No leftover .tmp files
    assert not list(ext_dir.glob("*.tmp"))


# ---------- list_extractions ----------


def _write_record(ext_dir: Path, eid: str, source_ref: str, created_at: str, fields=None) -> None:
    payload = {
        "extraction_id": eid,
        "source_type": "url",
        "source_ref": source_ref,
        "created_at": created_at,
        "params": {},
        "textin_response": {"metadata": {"page_count": 1}, "elements": [], "pages": []},
        "fields": fields or [],
        "field_source": "heuristic",
    }
    (ext_dir / f"{eid}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def test_list_extractions_empty(
    settings: TextInSettings, storage_dirs: tuple[Path, Path]
) -> None:
    items = list_extractions(settings)
    assert items == []


def test_list_extractions_sorted_newest_first(
    settings: TextInSettings, storage_dirs: tuple[Path, Path]
) -> None:
    ext_dir, _ = storage_dirs
    _write_record(ext_dir, "11111111-1111-4111-8111-111111111111", "a", "2026-01-01T00:00:00+00:00")
    _write_record(ext_dir, "22222222-2222-4222-8222-222222222222", "b", "2026-02-01T00:00:00+00:00")
    _write_record(ext_dir, "33333333-3333-4333-8333-333333333333", "c", "2026-03-01T00:00:00+00:00")

    items = list_extractions(settings)
    assert [i.source_ref for i in items] == ["c", "b", "a"]


def test_list_extractions_skips_corrupted(
    settings: TextInSettings, storage_dirs: tuple[Path, Path]
) -> None:
    ext_dir, _ = storage_dirs
    _write_record(ext_dir, "11111111-1111-4111-8111-111111111111", "good", "2026-01-01T00:00:00+00:00")
    # write a corrupt file
    (ext_dir / "corrupt.json").write_text("not json", encoding="utf-8")

    items = list_extractions(settings)
    assert len(items) == 1
    assert items[0].source_ref == "good"


# ---------- get_extraction ----------


def test_get_extraction_success(
    settings: TextInSettings, storage_dirs: tuple[Path, Path]
) -> None:
    ext_dir, _ = storage_dirs
    _write_record(
        ext_dir,
        "11111111-1111-4111-8111-111111111111",
        "https://example.com/x",
        "2026-01-01T00:00:00+00:00",
        fields=[{"page_number": 0, "name": "a", "value": "1", "source": "markdown"}],
    )

    rec = get_extraction(settings, "11111111-1111-4111-8111-111111111111")
    assert rec.extraction_id == "11111111-1111-4111-8111-111111111111"
    assert rec.source_ref == "https://example.com/x"
    assert len(rec.fields) == 1


def test_get_extraction_unknown(
    settings: TextInSettings, storage_dirs: tuple[Path, Path]
) -> None:
    with pytest.raises(ExtractionNotFound):
        get_extraction(settings, "11111111-1111-4111-8111-111111111111")


def test_get_extraction_invalid_id(
    settings: TextInSettings, storage_dirs: tuple[Path, Path]
) -> None:
    with pytest.raises(ExtractionNotFound):
        get_extraction(settings, "../bad")


def test_get_extraction_corrupt_raises_record_parse_error(
    settings: TextInSettings, storage_dirs: tuple[Path, Path]
) -> None:
    ext_dir, _ = storage_dirs
    (ext_dir / "11111111-1111-4111-8111-111111111111.json").write_text(
        "{not valid", encoding="utf-8"
    )
    with pytest.raises(RecordParseError):
        get_extraction(settings, "11111111-1111-4111-8111-111111111111")


# ---------- legacy record compat ----------


def test_get_extraction_back_computes_fields_when_missing(
    settings: TextInSettings, storage_dirs: tuple[Path, Path]
) -> None:
    """旧记录（无 fields 字段）按 heuristic 从 textin_response 重新计算。"""
    ext_dir, _ = storage_dirs
    payload = {
        "extraction_id": "11111111-1111-4111-8111-111111111111",
        "source_type": "url",
        "source_ref": "https://example.com/x",
        "created_at": "2026-01-01T00:00:00+00:00",
        "params": {},
        "textin_response": {"markdown": "发票号: LEGACY-1"},
    }
    (ext_dir / "11111111-1111-4111-8111-111111111111.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    rec = get_extraction(settings, "11111111-1111-4111-8111-111111111111")
    assert len(rec.fields) == 1
    assert rec.fields[0].name == "发票号"