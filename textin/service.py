"""OCR 业务编排（同步版）：调用 TextIn → 持久化原始 JSON → 计算摘要。

字段来源分两种路径：
- schema：用户声明字段名 → 调 TextIn /api/xparse/extract/sync（精确抽取）
- heuristic：从 markdown + 表格 cells 启发式配对（旧行为，作为未声明字段时的回退）

迁移自 saiflow-srv/app/services/ocr_service.py；主要变化：
  - async → sync（Flask/WSGI 兼容）
  - 新增 UUID 模式校验防止 <path:extraction_id> 路径穿越
  - 落盘记录损坏时抛 RecordParseError（路由映射 500）
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from mimetypes import guess_extension
from pathlib import Path
from typing import Any
from uuid import uuid4

from textin.client import TextInClient, parse_extracted_schema
from textin.config import TextInSettings
from textin.errors import ExtractionNotFound, RecordParseError, UploadedFileNotFound
from textin.field_extractor import extract_fields
from textin.schemas import (
    ExtractSummary,
    ExtractionListItem,
    ExtractionRecord,
    SourceType,
)

logger = logging.getLogger(__name__)

FieldSource = str  # "schema" | "heuristic"

# extraction_id 必须为 RFC 4122 UUID（小写 hex + 连字符），避免路径穿越。
EXTRACTION_ID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def is_valid_extraction_id(extraction_id: str) -> bool:
    return bool(EXTRACTION_ID_PATTERN.match(extraction_id))


def _storage_path(storage_dir: Path, extraction_id: str) -> Path:
    return storage_dir / f"{extraction_id}.json"


def safe_extension(content_type: str | None, filename: str | None) -> str:
    """根据 content_type / filename 推导一个安全的扩展名（仅用于本地落盘）。

    危险后缀（.htm/.html/.svg/.js）会被强制改写为 .bin，避免被误当作可执行内容处理。
    """
    ext: str | None = None
    if content_type:
        ext = guess_extension(content_type.split(";")[0].strip())
    if not ext and filename:
        _, _, e = filename.rpartition(".")
        if e:
            ext = "." + e
    ext = (ext or ".bin").lower()
    if ext in (".htm", ".html", ".svg", ".js"):
        ext = ".bin"
    return ext


def _save_uploaded_file(
    uploads_dir: Path,
    extraction_id: str,
    content: bytes,
    content_type: str | None,
    filename: str | None,
) -> Path | None:
    """保存原始上传文件到 uploads_dir；URL 场景不需要（返回 None）。"""
    if not content:
        return None
    uploads_dir.mkdir(parents=True, exist_ok=True)
    ext = safe_extension(content_type, filename)
    target = uploads_dir / f"{extraction_id}{ext}"
    target.write_bytes(content)
    return target


def find_uploaded_file(uploads_dir: Path, extraction_id: str) -> Path:
    """按 extraction_id 查找上传文件；任意后缀都匹配。"""
    if not is_valid_extraction_id(extraction_id):
        raise UploadedFileNotFound(extraction_id)
    if not uploads_dir.exists():
        raise UploadedFileNotFound(extraction_id)
    matches = list(uploads_dir.glob(f"{extraction_id}.*"))
    if not matches:
        raise UploadedFileNotFound(extraction_id)
    return matches[0]


def summarize(
    textin_response: dict[str, Any],
    *,
    fields: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """从 parse/sync 响应里提取 page_count / item_count / duration_ms / field_count。

    返回 dict 而非 Pydantic 模型（路由层负责 Serialize）。
    """
    metadata = textin_response.get("metadata") or {}
    pages = textin_response.get("pages") or []
    elements = textin_response.get("elements") or []

    page_count = metadata.get("page_count")
    if page_count is None and pages:
        page_count = len(pages)
    if page_count is None:
        page_count = 1

    duration = textin_response.get("duration")
    if fields is None:
        fields = extract_fields(textin_response)
    return {
        "page_count": int(page_count),
        "item_count": len(elements),
        "duration_ms": int(duration) if isinstance(duration, (int, float)) else None,
        "field_count": len(fields),
    }


def _write_record(
    storage_dir: Path,
    extraction_id: str,
    source_type: SourceType,
    source_ref: str,
    params: dict[str, Any],
    textin_response: dict[str, Any],
    fields: list[dict[str, Any]],
    field_source: FieldSource,
) -> Path:
    record = {
        "extraction_id": extraction_id,
        "source_type": source_type,
        "source_ref": source_ref,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "params": params,
        "textin_response": textin_response,
        "fields": fields,
        "field_source": field_source,
    }
    path = _storage_path(storage_dir, extraction_id)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def _load_record_fields(data: dict[str, Any]) -> tuple[list[dict[str, Any]], FieldSource]:
    """从落盘 JSON 取 (fields, field_source)；旧记录（无字段）按 heuristic 现算。"""
    field_source: FieldSource = data.get("field_source") or "heuristic"
    if "fields" in data and isinstance(data["fields"], list):
        return list(data["fields"]), field_source
    return extract_fields(data["textin_response"]), "heuristic"


def run_extraction(
    settings: TextInSettings,
    client: TextInClient,
    source_type: SourceType,
    source_ref: str,
    *,
    file_bytes: bytes | None = None,
    content_type: str | None = None,
    params: dict[str, Any] | None = None,
    field_names: list[str] | None = None,
) -> dict[str, Any]:
    """调用 TextIn 并落盘；返回 dict 供 router 序列化为 ExtractResponse。

    field_names 非空 → 走 schema 路径（额外调 /api/xparse/extract/sync）；
    否则按启发式提取（回退）。
    """
    params = params or {}
    field_names = [n for n in (field_names or []) if n and n.strip()]

    if source_type == "file":
        if file_bytes is None or content_type is None:
            raise ValueError("file source requires file_bytes and content_type")
        textin_response = client.extract_file(source_ref, file_bytes, content_type, params)
    else:
        textin_response = client.extract_url(source_ref, params)

    fields: list[dict[str, Any]]
    field_source: FieldSource
    if field_names:
        schema_resp: dict[str, Any] | None = None
        try:
            if source_type == "file":
                schema_resp = client.extract_with_schema(
                    filename=source_ref,
                    content=file_bytes or b"",
                    content_type=content_type or "application/octet-stream",
                    schema_fields=field_names,
                    parse_params=params,
                )
            else:
                # extract_with_schema 只接受本地文件，URL 走不了；URL 场景降级为启发式
                logger.warning(
                    "schema 抽取仅支持本地上传，URL 场景回退启发式: source_ref=%s", source_ref
                )
        except Exception as e:
            logger.warning("schema 抽取失败，回退启发式: %s", e)

        if schema_resp is not None:
            result = schema_resp.get("result") if isinstance(schema_resp, dict) else None
            extracted = result.get("extracted_schema") if isinstance(result, dict) else None
            fields = parse_extracted_schema(extracted)
            field_source = "schema"
        else:
            fields = extract_fields(textin_response)
            field_source = "heuristic"
    else:
        fields = extract_fields(textin_response)
        field_source = "heuristic"

    extraction_id = str(uuid4())
    if source_type == "file" and file_bytes:
        _save_uploaded_file(
            settings.ocr_uploads_dir,
            extraction_id,
            file_bytes,
            content_type,
            source_ref,
        )
    _write_record(
        settings.ocr_storage_dir,
        extraction_id,
        source_type,
        source_ref,
        params,
        textin_response,
        fields,
        field_source,
    )
    logger.info(
        "OCR extraction saved: id=%s source_type=%s field_source=%s fields=%d",
        extraction_id,
        source_type,
        field_source,
        len(fields),
    )

    return {
        "extraction_id": extraction_id,
        "source_type": source_type,
        "source_ref": source_ref,
        "summary": summarize(textin_response, fields=fields),
        "full_result": textin_response,
        "fields": fields,
    }


def list_extractions(settings: TextInSettings) -> list[ExtractionListItem]:
    """扫描 storage_dir，按 created_at 倒序返回摘要列表。"""
    storage_dir = settings.ocr_storage_dir
    if not storage_dir.exists():
        return []
    items: list[ExtractionListItem] = []
    for json_file in storage_dir.glob("*.json"):
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("跳过损坏的抽取记录 %s: %s", json_file.name, e)
            continue
        fields, _ = _load_record_fields(data)
        summary = summarize(data.get("textin_response") or {}, fields=fields)
        items.append(
            ExtractionListItem(
                extraction_id=data["extraction_id"],
                source_type=data["source_type"],
                source_ref=data["source_ref"],
                created_at=datetime.fromisoformat(data["created_at"]),
                page_count=summary["page_count"],
                item_count=summary["item_count"],
                field_count=summary["field_count"],
            )
        )
    items.sort(key=lambda x: x.created_at, reverse=True)
    return items


def get_extraction(settings: TextInSettings, extraction_id: str) -> ExtractionRecord:
    """读取单条完整记录；不存在时抛 ExtractionNotFound。"""
    if not is_valid_extraction_id(extraction_id):
        raise ExtractionNotFound(extraction_id)
    path = _storage_path(settings.ocr_storage_dir, extraction_id)
    if not path.exists():
        raise ExtractionNotFound(extraction_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise RecordParseError(str(e)) from e
    fields, _ = _load_record_fields(data)
    return ExtractionRecord(
        extraction_id=data["extraction_id"],
        source_type=data["source_type"],
        source_ref=data["source_ref"],
        created_at=datetime.fromisoformat(data["created_at"]),
        params=data.get("params") or {},
        textin_response=data["textin_response"],
        fields=fields,
    )