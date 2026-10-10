"""上传会话、文件与 URL 导入（PRD §8、§14.2）。

三段式：POST /uploads 预检建会话 → PUT /uploads/{id}/content 流式收内容
→ POST /uploads/{id}/complete 校验完整性并置 READY。
状态机：UPLOADING → UPLOADED → VALIDATING → READY / REJECTED。
"""
from __future__ import annotations

import hashlib
import io
import logging
import os
import re
import uuid

from pathlib import Path

import httpx
from flask import Blueprint, current_app, request, send_file
from werkzeug.exceptions import RequestEntityTooLarge

from auth import audit, current_user
from errors import ApiError, invalid_argument, not_found, ok
from extensions import db
from fileaccess import get_file_for_user
from models import File
from workspace import resolve_workspace

logger = logging.getLogger(__name__)
bp = Blueprint("files", __name__)

_EXT_MIME = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "pdf": "application/pdf",
}
_MAGIC = {
    "jpg": (b"\xff\xd8\xff",),
    "png": (b"\x89PNG\r\n\x1a\n",),
    "pdf": (b"%PDF",),
}
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._\-\u0e00-\u0e7f\u4e00-\u9fff ]+")


def _storage_dir() -> Path:
    d = Path(current_app.config["FILE_STORAGE_DIR"]).resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sniff_ext(data: bytes) -> str | None:
    for ext, magics in _MAGIC.items():
        for magic in magics:
            if data[: len(magic)] == magic:
                return ext
    return None


def _detect_page_count(ext: str, data: bytes) -> int:
    if ext != "pdf":
        return 1
    try:
        from pypdf import PdfReader

        return len(PdfReader(io.BytesIO(data)).pages)
    except Exception:  # noqa: BLE001 损坏/加密 PDF 在 complete 阶段拒绝
        raise ApiError(400, "INVALID_ARGUMENT", "files.corrupt_or_encrypted")


def _reject(f: File, reason_key: str) -> None:
    f.status = "REJECTED"
    f.reject_reason = reason_key
    db.session.commit()


def _save_content(f: File, data: bytes, ext: str) -> None:
    path = _storage_dir() / f"{f.id}.{ext}"
    path.write_bytes(data)
    f.storage_path = str(path)
    f.size_bytes = len(data)
    f.sha256 = hashlib.sha256(data).hexdigest()
    f.mime_type = _EXT_MIME[ext]


# ---------- 上传会话 ----------


@bp.post("/uploads")
def create_upload():
    """预检归属/用途/格式/限额，创建 UPLOADING 会话。"""
    body = request.get_json(silent=True) or {}
    user = current_user()
    ws_type, enterprise_id = resolve_workspace(
        user, body.get("workspace_type"), body.get("enterprise_id")
    )
    purpose = body.get("purpose") or "OCR_TASK"
    if purpose not in ("OCR_TASK", "APPLICATION_MATERIAL"):
        raise invalid_argument("error.invalid_argument")
    original_name = _SAFE_NAME.sub("_", str(body.get("file_name") or ""))[:255]
    ext = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else ""
    if ext not in current_app.config["ALLOWED_FILE_EXTENSIONS"]:
        raise invalid_argument(
            "files.unsupported_type",
            field_errors=[{"field": "file_name", "message_key": "files.unsupported_type"}],
        )
    declared_size = body.get("size_bytes")
    if declared_size is not None and int(declared_size) > current_app.config["MAX_FILE_SIZE_BYTES"]:
        raise invalid_argument("files.too_large")

    f = File(
        workspace_type=ws_type,
        enterprise_id=enterprise_id,
        created_by_user_id=user.id,
        uploader_user_id=user.id,
        purpose=purpose,
        original_name=original_name,
        mime_type=_EXT_MIME.get(ext, ""),
        status="UPLOADING",
    )
    db.session.add(f)
    db.session.commit()
    return ok({"file": f.to_dict(), "upload_id": f.id}, status=201)


@bp.put("/uploads/<upload_id>/content")
def upload_content(upload_id: str):
    """接收原始字节，校验魔数/大小，置 UPLOADED。"""
    f = db.session.get(File, upload_id)
    if f is None or f.created_by_user_id != current_user().id:
        raise not_found()
    if f.status not in ("UPLOADING", "UPLOADED", "REJECTED"):
        raise ApiError(409, "STATE_CONFLICT", "error.state_conflict")

    limit = current_app.config["MAX_FILE_SIZE_BYTES"]
    data = request.get_data(cache=False)
    if len(data) > limit:
        _reject(f, "files.too_large")
        raise invalid_argument("files.too_large")
    ext = f.original_name.rsplit(".", 1)[-1].lower() if "." in f.original_name else ""
    sniffed = _sniff_ext(data)
    if sniffed is None or (ext in ("jpg", "jpeg") and sniffed not in ("jpg",)) or (
        ext == "png" and sniffed != "png"
    ) or (ext == "pdf" and sniffed != "pdf"):
        _reject(f, "files.disguised_type")
        raise invalid_argument("files.disguised_type")

    _save_content(f, data, ext)
    f.status = "UPLOADED"
    f.reject_reason = ""
    db.session.commit()
    return ok({"file_id": f.id, "size_bytes": f.size_bytes, "sha256": f.sha256})


@bp.post("/uploads/<upload_id>/complete")
def upload_complete(upload_id: str):
    """确认上传并校验：完整性、页数、任务限额，置 READY。"""
    f = db.session.get(File, upload_id)
    if f is None or f.created_by_user_id != current_user().id:
        raise not_found()
    if f.status == "READY":
        return ok({"file": f.to_dict()})  # 幂等
    if f.status != "UPLOADED":
        raise ApiError(409, "STATE_CONFLICT", "error.state_conflict")

    f.status = "VALIDATING"
    db.session.commit()

    data = Path(f.storage_path).read_bytes()
    ext = f.original_name.rsplit(".", 1)[-1].lower()
    try:
        pages = _detect_page_count(ext, data)
    except ApiError as e:
        _reject(f, e.message_key)
        raise
    if f.purpose == "OCR_TASK":
        if ext == "pdf" and pages > current_app.config["MAX_PAGES_PER_TASK"]:
            _reject(f, "files.too_many_pages")
            raise invalid_argument("files.too_many_pages")
        if ext in current_app.config["ALLOWED_IMAGE_EXTENSIONS"] and pages > 1:
            _reject(f, "files.invalid")
            raise invalid_argument("files.invalid")

    f.page_count = pages
    f.status = "READY"
    db.session.commit()
    audit("file.upload_complete", "file", f.id, {"pages": pages, "bytes": f.size_bytes})
    return ok({"file": f.to_dict()})


# ---------- URL 导入 ----------


@bp.post("/file-imports")
def file_import():
    """受控公网抓取：http(s) only、20MB 上限、魔数校验，直接置 READY。"""
    from urllib.parse import urlparse

    body = request.get_json(silent=True) or {}
    user = current_user()
    ws_type, enterprise_id = resolve_workspace(
        user, body.get("workspace_type"), body.get("enterprise_id")
    )
    url = str(body.get("url") or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise invalid_argument("file_import.invalid_url")

    limit = current_app.config["MAX_FILE_SIZE_BYTES"]
    try:
        with httpx.Client(
            timeout=httpx.Timeout(20.0, connect=10.0),
            follow_redirects=True,
            headers={"User-Agent": "saiflow-ocr/1.2"},
        ) as client:
            with client.stream("GET", url) as resp:
                if resp.status_code != 200:
                    raise ApiError(400, "INVALID_ARGUMENT", "file_import.fetch_failed")
                chunks: list[bytes] = []
                total = 0
                for chunk in resp.iter_bytes(65536):
                    total += len(chunk)
                    if total > limit:
                        raise invalid_argument("files.too_large")
                    chunks.append(chunk)
                data = b"".join(chunks)
    except httpx.HTTPError:
        raise ApiError(400, "INVALID_ARGUMENT", "file_import.fetch_failed")

    ext = _sniff_ext(data)
    if ext is None:
        raise invalid_argument("files.unsupported_type")
    name = _SAFE_NAME.sub("_", os.path.basename(parsed.path) or f"import.{ext}")[:255]
    if not name.lower().endswith(f".{ext}"):
        name = f"{name or 'import'}.{ext}"

    f = File(
        workspace_type=ws_type,
        enterprise_id=enterprise_id,
        created_by_user_id=user.id,
        uploader_user_id=user.id,
        purpose="OCR_TASK",
        original_name=name,
        status="VALIDATING",
    )
    _save_content(f, data, ext)
    try:
        pages = _detect_page_count(ext, data)
    except ApiError as e:
        f.status = "REJECTED"
        f.reject_reason = e.message_key
        db.session.add(f)
        db.session.commit()
        raise
    f.page_count = pages
    f.status = "READY"
    db.session.add(f)
    db.session.commit()
    audit("file.import", "file", f.id, {"source_url": url[:128], "pages": pages})
    return ok({"file": f.to_dict()}, status=201)


# ---------- 文件读取 ----------


@bp.get("/files/<file_id>")
def file_info(file_id: str):
    f = get_file_for_user(file_id, current_user())
    return ok({"file": f.to_dict()})


@bp.get("/files/<file_id>/content")
def file_content(file_id: str):
    """原件读取/下载；Range 由 send_file(conditional=True) 处理，同样过对象鉴权。"""
    f = get_file_for_user(file_id, current_user())
    if f.status not in ("READY", "UPLOADED"):
        raise not_found()
    path = Path(f.storage_path)
    if not path.exists():
        raise not_found()
    download = request.args.get("download") == "1"
    return send_file(
        str(path),
        mimetype=f.mime_type,
        as_attachment=download,
        download_name=f.original_name if download else None,
        conditional=True,
    )


@bp.get("/files/<file_id>/pages/<int:page>/preview")
def file_page_preview(file_id: str, page: int):
    """页预览：图片返回本体；PDF 用 PyMuPDF 渲染为 PNG（按文件缓存，鉴权不变）。"""
    f = get_file_for_user(file_id, current_user())
    ext = f.original_name.rsplit(".", 1)[-1].lower()
    if f.status not in ("READY", "UPLOADED"):
        raise not_found()
    if ext == "pdf":
        png = _render_pdf_page(f, page)
        return send_file(png, mimetype="image/png", conditional=True)
    if page != 1:
        raise not_found()
    return send_file(str(Path(f.storage_path)), mimetype=f.mime_type, conditional=True)


def _render_pdf_page(f: File, page: int) -> str:
    """渲染 PDF 单页为 PNG 并缓存到 <storage>/_previews/<file_id>/page_<n>.png，返回缓存路径。"""
    import pymupdf

    cache_dir = _storage_dir() / "_previews" / f.id
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / f"page_{page}.png"
    if cached.exists():
        return str(cached)

    src = Path(f.storage_path)
    if not src.exists():
        raise not_found()
    try:
        doc = pymupdf.open(str(src))
        with doc:
            if page < 1 or page > doc.page_count:
                raise not_found()
            pix = doc.load_page(page - 1).get_pixmap(matrix=pymupdf.Matrix(2, 2))
            pix.save(str(cached))
    except pymupdf.FileDataError:
        raise ApiError(400, "INVALID_ARGUMENT", "files.corrupt_or_encrypted")
    return str(cached)


# ---------- 413 ----------


@bp.app_errorhandler(RequestEntityTooLarge)
def _too_large(_e):
    max_bytes = current_app.config.get("MAX_CONTENT_LENGTH", 0)
    return (
        {
            "code": "INVALID_ARGUMENT",
            "message_key": "files.too_large",
            "request_id": uuid.uuid4().hex[:16],
            "retryable": False,
            "field_errors": [{"field": "file", "message_key": "files.too_large", "limit_bytes": max_bytes}],
        },
        400,
    )
