"""TextIn 结果规范化（PRD §12.2：表格/文字/字段标准化、来源映射）。

把 parse/sync（或 extract/sync）响应统一成 PRD 结果结构：
    pages: [{page_number, text, tables, fields}]
    tables.cells: {row_index,column_index,row_span,column_span,
                   raw_text,current_text,polygon,confidence,review_status}
原始响应不落库（大 JSON 只保留审计副本到磁盘），库中只存标准化结果。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from textin.client import TextInClient, parse_extracted_schema
from textin.errors import TextInAPIError
from textin.field_extractor import extract_fields

logger = logging.getLogger(__name__)

VENDOR = "textin"


def _num(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _find(response: dict[str, Any], key: str) -> Any:
    """parse/sync 的结果在顶层；extract/sync 的解析结果可能包在 result 里。"""
    if key in response:
        return response[key]
    result = response.get("result")
    if isinstance(result, dict) and key in result:
        return result[key]
    return None


def _page_numbers(response: dict[str, Any]) -> list[int]:
    pages = _find(response, "pages") or []
    out: list[int] = []
    for p in pages:
        if isinstance(p, dict) and p.get("page_number") is not None:
            out.append(_num(p["page_number"], -1))
    if not out:
        for e in _find(response, "elements") or []:
            if isinstance(e, dict) and e.get("page_number") is not None:
                n = _num(e["page_number"], -1)
                if n > 0 and n not in out:
                    out.append(n)
    return sorted(n for n in out if n > 0)


def _page_texts(response: dict[str, Any]) -> dict[int, str]:
    pages = _find(response, "pages") or []
    out: dict[int, str] = {}
    for p in pages:
        if not isinstance(p, dict):
            continue
        n = _num(p.get("page_number"), 0)
        md = (p.get("markdown") or "").strip()
        if n > 0 and md:
            out[n] = md
    for e in _find(response, "elements") or []:
        # TextIn 文字块 type 为 NarrativeText/Title/TableCaption 等；Table 的 text 是 HTML 表格，跳过
        if not isinstance(e, dict) or e.get("type") == "Table":
            continue
        n = _num(e.get("page_number"), 0)
        text = (e.get("text") or "").strip().replace("**", "")
        if n > 0 and text:
            out[n] = f"{out[n]}\n{text}".strip() if n in out else text
    return out


def _normalize_tables(response: dict[str, Any]) -> dict[int, list[dict[str, Any]]]:
    out: dict[int, list[dict[str, Any]]] = {}
    seq = 0
    for e in _find(response, "elements") or []:
        if not isinstance(e, dict) or e.get("type") != "Table":
            continue
        seq += 1
        page = _num(e.get("page_number"), 1)
        ts = e.get("table_structure") or {}
        cells_in = ts.get("cells") or []
        cells: list[dict[str, Any]] = []
        max_row = max_col = 0
        for c in cells_in:
            if not isinstance(c, dict):
                continue
            row = _num(c.get("row"))
            col = _num(c.get("col"))
            row_span = max(1, _num(c.get("row_span"), 1))
            col_span = max(1, _num(c.get("col_span"), 1))
            text = str(c.get("text") or "")
            max_row = max(max_row, row + row_span)
            max_col = max(max_col, col + col_span)
            cells.append(
                {
                    "row_index": row,
                    "column_index": col,
                    "row_span": row_span,
                    "column_span": col_span,
                    "raw_text": text,
                    "current_text": text,
                    "polygon": c.get("polygon") or c.get("coordinates") or [],
                    "confidence": float(c.get("confidence") or 0),
                    "review_status": "UNREVIEWED",
                }
            )
        # 第一行约定为表头：跳过不进 fields，但保留在表格结构里
        out.setdefault(page, []).append(
            {
                "table_id": f"table_{seq}",
                "row_count": max_row,
                "column_count": max_col,
                "cells": cells,
            }
        )
    return out


def _fields_for_page(fields: list[dict[str, Any]], page: int) -> list[dict[str, Any]]:
    return [
        {"name": f["name"], "value": f["value"], "source": f.get("source", ""), "source_page": f.get("page_number", 0)}
        for f in fields
        if f.get("page_number", 0) in (0, page)
    ]


def normalize_response(
    response: dict[str, Any],
    *,
    fields: list[dict[str, Any]],
    page_start: int = 1,
    page_count: int | None = None,
) -> dict[str, Any]:
    """标准化主入口。page_start/page_count 为 1 基请求范围，用于缺失页计算。"""
    page_numbers = _page_numbers(response)
    texts = _page_texts(response)
    tables = _normalize_tables(response)

    requested_end = (page_start + page_count - 1) if page_count else None
    if requested_end:
        expected = set(range(page_start, requested_end + 1))
    else:
        expected = set(page_numbers) or {page_start}
    missing = sorted(expected - set(page_numbers))

    pages = []
    for n in sorted(set(page_numbers) | (expected - set(missing))):
        pages.append(
            {
                "page_number": n,
                "text": texts.get(n, ""),
                "tables": tables.get(n, []),
                "fields": _fields_for_page(fields, n),
            }
        )

    table_count = sum(len(t) for t in tables.values())
    duration = response.get("duration")
    metadata = _find(response, "metadata") or {}
    return {
        "vendor": VENDOR,
        "page_count": _num(metadata.get("page_count"), len(page_numbers) or 1),
        "duration_ms": _num(duration) if isinstance(duration, (int, float)) else 0,
        "pages": pages,
        "missing_pages": missing,
        "table_count": table_count,
        "field_count": len(fields),
    }


def run_extraction(
    client: TextInClient,
    *,
    filename: str,
    content: bytes,
    content_type: str,
    params: dict[str, Any],
    field_names: list[str] | None = None,
) -> dict[str, Any]:
    """唯一 OCR 调用路径：TextIn 单厂商（PRD §12），永不回退其他厂商。

    field_names 非空 → extract/sync（schema 字段，含解析）；否则 parse/sync。
    schema 失败 → 回退启发式字段（仍属 TextIn 输出）。
    """
    field_names = [n.strip() for n in (field_names or []) if n and n.strip()]
    # 任务参数 page 为 0 基（与 TextIn config 一致）；缺失页按 1 基计算
    page_start = _num(params.get("page"), 0) + 1
    page_count = params.get("page_count")

    if field_names:
        try:
            response = client.extract_with_schema(
                filename=filename,
                content=content,
                content_type=content_type,
                schema_fields=field_names,
                parse_params=params,
            )
        except TextInAPIError:
            # schema 抽取失败（如厂商抽取阶段不可用）：回退 parse/sync + 启发式字段，
            # 仍属 TextIn 输出，不切换其他厂商（PRD §12）。
            logger.exception("TextIn extract/sync failed; falling back to parse/sync")
            response = client.extract_file(filename, content, content_type, params)
            fields = extract_fields(response)
            field_source = "heuristic"
        else:
            result = response.get("result") if isinstance(response.get("result"), dict) else response
            extracted = result.get("extracted_schema")
            fields = parse_extracted_schema(extracted)
            field_source = "schema"
            if not fields:  # schema 未命中：用启发式兜底字段
                fields = extract_fields(response)
                field_source = "heuristic"
    else:
        response = client.extract_file(filename, content, content_type, params)
        fields = extract_fields(response)
        field_source = "heuristic"

    normalized = normalize_response(
        response, fields=fields, page_start=page_start,
        page_count=_num(page_count) if page_count else None,
    )
    normalized["field_source"] = field_source
    return normalized


def persist_raw_audit_copy(storage_dir: str, job_id: str, payload: dict[str, Any]) -> str:
    """原始厂商响应的审计副本（PRD §15 操作追踪），库中只存引用路径。"""
    d = Path(storage_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{job_id}.json"
    path.write_text(
        json.dumps(
            {"job_id": job_id, "saved_at": datetime.now(timezone.utc).isoformat(), **payload},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return str(path)


__all__ = [
    "VENDOR",
    "TextInAPIError",
    "normalize_response",
    "run_extraction",
    "persist_raw_audit_copy",
]
