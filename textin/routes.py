"""OCR HTTP 路由：/api/v1/ocr/*

迁移自 saiflow-srv/app/routers/ocr.py；主要变化：
  - FastAPI router → Flask Blueprint
  - Depends → current_app.extensions["textin_settings"] / ["textin_client"]
  - UploadFile / Form() → request.files / request.form
  - 错误信封统一为 {code, msg, data}（蓝本级 errorhandler 渲染）
"""

from __future__ import annotations

import logging
import mimetypes
from typing import Any

from flask import Blueprint, Response, current_app, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

from textin.client import TextInClient
from textin.config import TextInSettings
from textin.errors import (
    ExtractionNotFound,
    OcrHTTPError,
    RecordParseError,
    TextInAPIError,
    UploadedFileNotFound,
)
from textin.service import (
    EXTRACTION_ID_PATTERN,
    find_uploaded_file,
    get_extraction,
    is_valid_extraction_id,
    list_extractions,
    run_extraction,
)

logger = logging.getLogger(__name__)

ocr_bp = Blueprint("textin_ocr", __name__)


# ---------- helpers ----------


def _settings() -> TextInSettings:
    s: TextInSettings | None = current_app.extensions.get("textin_settings")
    if s is None:  # pragma: no cover — init_textin 保证一定存在
        raise OcrHTTPError(500, "internal_error", "TextIn settings 未初始化")
    return s


def _client() -> TextInClient | None:
    return current_app.extensions.get("textin_client")


def _fail(status: int, code: str, msg: str, data: Any = None) -> "OcrHTTPError":
    return OcrHTTPError(status, code, msg, data)


def _parse_bool(v: str | None) -> bool | None:
    if v is None:
        return None
    s = v.strip().lower()
    if s in ("true", "1", "yes", "on"):
        return True
    if s in ("false", "0", "no", "off"):
        return False
    raise _fail(422, "validation_failed", f"无法解析布尔值: {v!r}")


def _parse_int(v: str | None) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        raise _fail(422, "validation_failed", f"无法解析整数: {v!r}")


def _ok(data: Any, msg: str = "ok") -> tuple[Response, int]:
    return jsonify(code=200, msg=msg, data=data), 200


def _require_uuid(extraction_id: str) -> None:
    """Reject non-UUID paths early with 404 — never let them reach filesystem code."""
    if not is_valid_extraction_id(extraction_id):
        raise _fail(404, "not_found", f"extraction_id={extraction_id} 不存在")


# ---------- error rendering ----------


@ocr_bp.errorhandler(OcrHTTPError)
def _handle_ocr_http_error(error: OcrHTTPError):
    return (
        jsonify(code=error.status, msg=error.msg, data=error.data),
        error.status,
    )


@ocr_bp.errorhandler(RequestEntityTooLarge)
def _handle_too_large(_):
    max_bytes = _settings().max_upload_bytes
    return (
        jsonify(
            code=413,
            msg=f"文件超过 {max_bytes} 字节上限",
            data=None,
        ),
        413,
    )


# ---------- routes ----------


@ocr_bp.post("/extract")
def extract():
    if _client() is None:
        raise _fail(503, "service_unavailable", "TextIn 服务未配置")

    source_type = request.form.get("source_type")
    if source_type not in ("file", "url"):
        raise _fail(422, "validation_failed", "source_type 必须为 file 或 url")

    field_names = request.form.getlist("field_names") or request.form.getlist("field_names[]")

    params: dict[str, Any] = {}
    try:
        for k, parser in (
            ("parse_mode", lambda v: v if v else None),
            ("language", lambda v: v if v else None),
            ("table", _parse_bool),
            ("formula", _parse_bool),
            ("rotate", _parse_bool),
            ("page", _parse_int),
            ("page_count", _parse_int),
        ):
            raw = request.form.get(k)
            if raw is not None and raw != "":
                val = parser(raw)
                if val is not None:
                    params[k] = val
    except OcrHTTPError:
        raise
    except Exception:  # noqa: BLE001
        raise _fail(422, "validation_failed", "参数解析失败")

    if source_type == "file":
        file = request.files.get("file")
        if file is None or not file.filename:
            raise _fail(422, "validation_failed", "source_type=file 时必须上传文件")
        content = file.read()
        settings = _settings()
        if len(content) == 0:
            raise _fail(400, "invalid_format", "上传文件为空")
        if len(content) > settings.max_upload_bytes:
            raise _fail(413, "invalid_format", f"文件超过 {settings.max_upload_bytes} 字节上限")
        try:
            result = run_extraction(
                settings,
                _client(),  # type: ignore[arg-type]
                "file",
                file.filename or "uploaded",
                file_bytes=content,
                content_type=file.content_type or "application/octet-stream",
                params=params,
                field_names=field_names,
            )
        except TextInAPIError as e:
            raise _fail(502, "validation_failed", str(e))
        return _ok(result, msg="提取成功")

    # source_type == "url"
    url = request.form.get("url")
    if not url:
        raise _fail(422, "validation_failed", "source_type=url 时必须提供 url")
    try:
        result = run_extraction(
            _settings(),
            _client(),  # type: ignore[arg-type]
            "url",
            url,
            params=params,
            field_names=field_names,
        )
    except TextInAPIError as e:
        raise _fail(502, "validation_failed", str(e))
    return _ok(result, msg="提取成功")


@ocr_bp.get("/extractions")
def list_extractions_route():
    return _ok([i.model_dump(mode="json") for i in list_extractions(_settings())])


@ocr_bp.get("/extractions/<extraction_id>")
def get_extraction_route(extraction_id: str):
    _require_uuid(extraction_id)
    try:
        rec = get_extraction(_settings(), extraction_id)
    except ExtractionNotFound:
        raise _fail(404, "not_found", f"extraction_id={extraction_id} 不存在")
    except RecordParseError as e:
        logger.warning("Record parse failed for %s: %s", extraction_id, e)
        raise _fail(500, "internal_error", "记录解析失败")
    return _ok(rec.model_dump(mode="json"))


@ocr_bp.get("/extractions/<extraction_id>/file")
def get_extraction_file_route(extraction_id: str):
    _require_uuid(extraction_id)
    try:
        path = find_uploaded_file(_settings().ocr_uploads_dir, extraction_id)
    except UploadedFileNotFound:
        raise _fail(
            404,
            "not_found",
            f"extraction_id={extraction_id} 无原始文件（URL 抽取或已清理）",
        )
    content = path.read_bytes()
    content_type, _ = mimetypes.guess_type(path.name)
    response = Response(content, mimetype=content_type or "application/octet-stream")
    response.headers["Content-Disposition"] = f'inline; filename="{path.name}"'
    return response