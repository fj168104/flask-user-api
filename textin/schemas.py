"""OCR 请求/响应 Pydantic 模型。

迁移自 saiflow-srv/app/schemas/ocr.py；保留成功模型，丢弃原 ErrorEnvelope / ErrorResponse
（错误响应统一通过 textin/routes.py 渲染为 {code, msg, data} 信封）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

SourceType = Literal["file", "url"]
ExtractionStatus = Literal["success", "pending", "in_progress", "completed", "failed"]


class ExtractSummary(BaseModel):
    page_count: int
    item_count: int
    duration_ms: int | None = None
    field_count: int = 0


class ExtractedField(BaseModel):
    """启发式/Schema 抽取的 (字段名, 值) 对。"""

    page_number: int = 0
    name: str
    value: str
    source: Literal["table", "markdown", "schema"]


class ExtractResponse(BaseModel):
    """POST /api/v1/ocr/extract 的成功响应。"""

    extraction_id: str
    source_type: SourceType
    source_ref: str = Field(..., description="文件名（file）或 URL（url）")
    summary: ExtractSummary
    full_result: dict[str, Any] = Field(..., description="TextIn 完整原始 JSON")
    fields: list[ExtractedField] = Field(default_factory=list)


class ExtractionListItem(BaseModel):
    """GET /api/v1/ocr/extractions 的列表项。"""

    extraction_id: str
    source_type: SourceType
    source_ref: str
    created_at: datetime
    page_count: int
    item_count: int
    field_count: int = 0
    status: ExtractionStatus = "success"


class ExtractionRecord(BaseModel):
    """GET /api/v1/ocr/extractions/{id} 的完整记录。"""

    extraction_id: str
    source_type: SourceType
    source_ref: str
    created_at: datetime
    params: dict[str, Any] = Field(default_factory=dict)
    textin_response: dict[str, Any]
    fields: list[ExtractedField] = Field(default_factory=list)
    status: ExtractionStatus = "success"
    async_job_id: str | None = None


class AsyncSubmitResponse(BaseModel):
    """POST /api/v1/ocr/async/extract 的成功响应。"""

    extraction_id: str
    job_id: str
    status: Literal["pending"] = "pending"


class AsyncStatusResponse(BaseModel):
    """GET /api/v1/ocr/async/extractions/{id}/status 的成功响应。

    record 仅在 status == "completed" 时填充；pending / in_progress / failed 时为 None。
    """

    status: ExtractionStatus
    record: ExtractionRecord | None = None
    message: str | None = None