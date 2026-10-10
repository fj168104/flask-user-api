"""OCR 任务路由（PRD §9/§14.2）。

任务归属工作空间由源文件推导（文件与任务同空间），访问控制与 fileaccess 一致：
本人任务 + 本人生成，或 ENTERPRISE 空间 ACTIVE 成员可见。
幂等创建：Idempotency-Key（header 优先，body 兜底）命中已有任务时原样返回。
"""
from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, current_app, request
from sqlalchemy import and_, or_

from auth import audit, current_user
from errors import invalid_argument, not_found, ok, state_conflict
from extensions import db
from fileaccess import get_file_for_user
from models import File, Membership, OcrJob
from workspace import resolve_workspace

ocr_bp = Blueprint("ocr", __name__)

TERMINAL = {"SUCCEEDED", "PARTIAL_SUCCESS", "FAILED", "CANCELLED"}
RUNNING = {"QUEUED", "PREPROCESSING", "PROCESSING", "POSTPROCESSING"}
MAX_FIELD_NAMES = 100
MAX_FIELD_NAME_LEN = 64


def _active_enterprise_ids(user_id: int) -> list[int]:
    rows = Membership.query.filter_by(user_id=user_id, status="ACTIVE").all()
    return [m.enterprise_id for m in rows]


def _get_job_for_user(job_id: str, user) -> OcrJob:
    job = db.session.get(OcrJob, job_id)
    if job is None:
        raise not_found()
    if job.created_by_user_id == user.id:
        return job
    if job.workspace_type == "ENTERPRISE" and job.enterprise_id in _active_enterprise_ids(user.id):
        return job
    raise not_found()


def _summary(job: OcrJob) -> dict:
    file = db.session.get(File, job.file_id)
    return job.to_summary(file_name=file.original_name if file else "")


@ocr_bp.get("/ocr/capabilities")
def capabilities():
    cfg = current_app.config
    return ok(
        {
            "vendor": "textin",
            "features": {
                "table": True,
                "formula": True,
                "rotate": True,
                "fields": True,
                "parse_modes": ["scan", "layout", "article"],
            },
            "limits": {
                "allowed_extensions": sorted(cfg["ALLOWED_FILE_EXTENSIONS"]),
                "max_file_size_bytes": cfg["MAX_FILE_SIZE_BYTES"],
                "max_pages_per_task": cfg["MAX_PAGES_PER_TASK"],
                "max_images_per_task": cfg["MAX_IMAGES_PER_TASK"],
                "max_images_total_bytes": cfg["MAX_IMAGES_TOTAL_BYTES"],
            },
        }
    )


@ocr_bp.post("/ocr/jobs")
def create_job():
    user = current_user()
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        raise invalid_argument()

    file_id = body.get("file_id")
    if not file_id or not isinstance(file_id, str):
        raise invalid_argument(field_errors=[{"field": "file_id", "reason": "required"}])

    file = get_file_for_user(file_id, user)  # 跨用户直接 404
    if file.purpose != "OCR_TASK":
        raise invalid_argument("job.file_purpose_invalid")
    if file.status != "READY":
        raise state_conflict("job.file_not_ready")

    # 任务空间与文件一致；企业空间再校验一次成员资格（文件可见≠空间可用）
    if file.workspace_type == "ENTERPRISE":
        resolve_workspace(user, "ENTERPRISE", file.enterprise_id)
        workspace_type, enterprise_id = "ENTERPRISE", file.enterprise_id
    else:
        workspace_type, enterprise_id = "PERSONAL", None

    params = body.get("params") or {}
    if not isinstance(params, dict):
        raise invalid_argument(field_errors=[{"field": "params", "reason": "invalid"}])
    field_names = body.get("field_names")
    if field_names is not None:
        if (
            not isinstance(field_names, list)
            or len(field_names) > MAX_FIELD_NAMES
            or any(not isinstance(n, str) or not n.strip() or len(n) > MAX_FIELD_NAME_LEN for n in field_names)
        ):
            raise invalid_argument(field_errors=[{"field": "field_names", "reason": "invalid"}])
        field_names = [n.strip() for n in field_names if n.strip()]

    page = params.get("page")
    page_count = params.get("page_count")
    if page is not None and (not isinstance(page, int) or isinstance(page, bool) or page < 0):
        raise invalid_argument(field_errors=[{"field": "page", "reason": "invalid"}])
    if page_count is not None and (
        not isinstance(page_count, int) or isinstance(page_count, bool) or page_count < 1
    ):
        raise invalid_argument(field_errors=[{"field": "page_count", "reason": "invalid"}])
    if file.page_count:
        start = (page or 0) + 1
        span = page_count or min(file.page_count, current_app.config["MAX_PAGES_PER_TASK"])
        if start > file.page_count:
            raise invalid_argument("job.page_out_of_range")
        if span > current_app.config["MAX_PAGES_PER_TASK"]:
            raise invalid_argument("job.page_count_exceeded")

    idem = request.headers.get("Idempotency-Key") or body.get("idempotency_key") or None
    if idem is not None:
        if not isinstance(idem, str) or not idem.strip() or len(idem) > 128:
            raise invalid_argument(field_errors=[{"field": "idempotency_key", "reason": "invalid"}])
        idem = idem.strip()
        existing = OcrJob.query.filter_by(idempotency_key=idem).first()
        if existing is not None:
            owner = existing.created_by_user_id == user.id
            if owner or (
                existing.workspace_type == "ENTERPRISE"
                and existing.enterprise_id in _active_enterprise_ids(user.id)
            ):
                return ok(_summary(existing), status=200)  # 幂等重放
            raise not_found()

    stored_params = {
        k: params[k]
        for k in ("parse_mode", "table", "formula", "rotate", "page", "page_count")
        if k in params
    }
    if field_names:
        stored_params["field_names"] = field_names

    job = OcrJob(
        workspace_type=workspace_type,
        enterprise_id=enterprise_id,
        created_by_user_id=user.id,
        file_id=file.id,
        params=stored_params,
        capability={"vendor": "textin"},
        status="QUEUED",
        progress=0,
        idempotency_key=idem,
    )
    db.session.add(job)
    db.session.commit()
    audit("job.create", "ocr_job", job.id, {"file_id": file.id, "params": stored_params})
    return ok(_summary(job), status=201)


@ocr_bp.get("/ocr/jobs")
def list_jobs():
    user = current_user()
    q = request.args

    scope = or_(
        and_(
            OcrJob.workspace_type == "PERSONAL",
            OcrJob.created_by_user_id == user.id,
        ),
        OcrJob.enterprise_id.in_(_active_enterprise_ids(user.id) or [0]),
    )
    query = OcrJob.query.filter(scope)

    status = q.get("status")
    if status:
        wanted = [s.strip().upper() for s in status.split(",") if s.strip()]
        bad = [s for s in wanted if s not in TERMINAL | RUNNING]
        if bad:
            raise invalid_argument(field_errors=[{"field": "status", "reason": "invalid"}])
        query = query.filter(OcrJob.status.in_(wanted))

    ws = q.get("workspace_type")
    if ws:
        ws = ws.upper()
        if ws == "PERSONAL":
            query = query.filter(
                OcrJob.workspace_type == "PERSONAL",
                OcrJob.created_by_user_id == user.id,
            )
        elif ws == "ENTERPRISE":
            eid = q.get("enterprise_id", type=int)
            if not eid or eid not in _active_enterprise_ids(user.id):
                raise not_found()
            query = query.filter(
                OcrJob.workspace_type == "ENTERPRISE", OcrJob.enterprise_id == eid
            )
        else:
            raise invalid_argument(field_errors=[{"field": "workspace_type", "reason": "invalid"}])

    page = q.get("page", default=1, type=int)
    page_size = q.get("page_size", default=20, type=int)
    if page < 1 or page_size < 1 or page_size > 100:
        raise invalid_argument(field_errors=[{"field": "page", "reason": "invalid"}])

    total = query.count()
    rows = (
        query.order_by(OcrJob.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    file_ids = {j.file_id for j in rows}
    names = {
        f.id: f.original_name for f in File.query.filter(File.id.in_(file_ids)).all()
    } if file_ids else {}
    return ok(
        {
            "items": [j.to_summary(file_name=names.get(j.file_id, "")) for j in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    )


@ocr_bp.get("/ocr/jobs/<job_id>")
def get_job(job_id: str):
    job = _get_job_for_user(job_id, current_user())
    return ok(_summary(job))


@ocr_bp.post("/ocr/jobs/<job_id>/cancel")
def cancel_job(job_id: str):
    user = current_user()
    job = _get_job_for_user(job_id, user)
    if job.status in TERMINAL:
        raise state_conflict("job.already_terminal")
    if job.status == "CANCEL_REQUESTED":
        return ok(_summary(job))  # 幂等
    if job.status == "QUEUED":
        job.status = "CANCELLED"
        job.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
    else:
        job.status = "CANCEL_REQUESTED"
    db.session.commit()
    audit("job.cancel", "ocr_job", job.id)
    return ok(_summary(job))


@ocr_bp.post("/ocr/jobs/<job_id>/rerun")
def rerun_job(job_id: str):
    user = current_user()
    old = _get_job_for_user(job_id, user)
    if old.status not in TERMINAL:
        raise state_conflict("job.not_terminal")
    file = db.session.get(File, old.file_id)
    if file is None or file.status != "READY":
        raise state_conflict("job.file_not_ready")
    if file.workspace_type == "ENTERPRISE":
        resolve_workspace(user, "ENTERPRISE", file.enterprise_id)

    job = OcrJob(
        workspace_type=old.workspace_type,
        enterprise_id=old.enterprise_id,
        created_by_user_id=user.id,
        file_id=old.file_id,
        params=dict(old.params or {}),
        capability=dict(old.capability or {}),
        status="QUEUED",
        progress=0,
        parent_job_id=old.id,
    )
    db.session.add(job)
    db.session.commit()
    audit("job.rerun", "ocr_job", job.id, {"parent_job_id": old.id})
    return ok(_summary(job), status=201)


@ocr_bp.delete("/ocr/jobs/<job_id>")
def delete_job(job_id: str):
    user = current_user()
    job = _get_job_for_user(job_id, user)
    if job.status not in TERMINAL:
        raise state_conflict("job.not_terminal")

    from models import Export, PageResult, ResultVersion

    versions = ResultVersion.query.filter_by(job_id=job.id).all()
    version_ids = [v.id for v in versions]
    if version_ids:
        PageResult.query.filter(PageResult.result_version_id.in_(version_ids)).delete(
            synchronize_session=False
        )
        exports = Export.query.filter(Export.result_version_id.in_(version_ids)).all()
        for exp in exports:
            if exp.storage_path:
                from pathlib import Path

                try:
                    Path(exp.storage_path).unlink(missing_ok=True)
                except OSError:
                    pass
            db.session.delete(exp)
        for v in versions:
            db.session.delete(v)
    db.session.commit()
    audit("job.delete_results", "ocr_job", job.id)
    return ok({"id": job.id, "results_deleted": True})
