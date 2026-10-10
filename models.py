"""核心数据模型（PRD §13 的 P0 落地）。

覆盖：users / sessions / enterprises / memberships / applications /
invitations / files（含 upload_sessions 生命周期）/ ocr_jobs /
result_versions / page_results / exports / notifications / audit_events /
data_requests / review_sync_cursors。
大型结果 JSON 存 page_results.tables/text/fields（MySQL JSON 列），
原件字节存私有存储目录，库中只保存引用与索引。
"""
from __future__ import annotations

import uuid

from datetime import datetime, timezone

from extensions import db


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _bigint_pk():
    """MySQL 用 BIGINT AUTO_INCREMENT；SQLite 退化为 INTEGER 才能自增。"""
    return db.BigInteger().with_variant(db.Integer, "sqlite")


class TimestampMixin:
    created_at = db.Column(db.DateTime, nullable=False, default=_now)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=_now, onupdate=_now
    )


# ---------- 身份与会话 ----------


class User(db.Model, TimestampMixin):
    __tablename__ = "users"

    id = db.Column(_bigint_pk(), primary_key=True, autoincrement=True)
    line_provider = db.Column(db.String(32), nullable=False, default="line")
    line_sub = db.Column(db.String(128), nullable=False)
    display_name = db.Column(db.String(128), nullable=False, default="")
    avatar_url = db.Column(db.String(512), nullable=False, default="")
    language = db.Column(db.String(8), nullable=False, default="th")
    status = db.Column(db.String(16), nullable=False, default="ACTIVE")  # ACTIVE / DISABLED

    __table_args__ = (
        db.UniqueConstraint("line_provider", "line_sub", name="uq_users_line_identity"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "avatar_url": self.avatar_url,
            "language": self.language,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Session(db.Model):
    __tablename__ = "sessions"

    id = db.Column(_bigint_pk(), primary_key=True, autoincrement=True)
    token_hash = db.Column(db.String(64), nullable=False, unique=True)
    user_id = db.Column(db.BigInteger, db.ForeignKey("users.id"), nullable=False, index=True)
    csrf_hash = db.Column(db.String(64), nullable=False)
    issued_at = db.Column(db.DateTime, nullable=False, default=_now)
    absolute_expires_at = db.Column(db.DateTime, nullable=False)
    idle_expires_at = db.Column(db.DateTime, nullable=False)
    revoked_at = db.Column(db.DateTime, nullable=True)
    created_ip = db.Column(db.String(64), nullable=False, default="")

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    def is_expired(self, now: datetime | None = None) -> bool:
        now = now or _now()
        return now >= self.absolute_expires_at or now >= self.idle_expires_at

    def touch(self, idle_ttl_seconds: int) -> None:
        from datetime import timedelta

        self.idle_expires_at = _now() + timedelta(seconds=idle_ttl_seconds)


# ---------- 企业与成员 ----------


class Enterprise(db.Model, TimestampMixin):
    __tablename__ = "enterprises"

    id = db.Column(_bigint_pk(), primary_key=True, autoincrement=True)
    code = db.Column(db.String(32), nullable=False, unique=True)
    country = db.Column(db.String(8), nullable=False, default="TH")
    legal_name = db.Column(db.String(255), nullable=False, default="")
    registration_no = db.Column(db.String(64), nullable=False, default="")
    status = db.Column(
        db.String(32), nullable=False, default="PENDING_VERIFICATION"
    )  # PENDING_VERIFICATION / VERIFIED / SUSPENDED / DISABLED
    external_ref = db.Column(db.String(64), nullable=False, default="")
    policy_version = db.Column(db.Integer, nullable=False, default=1)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "code": self.code,
            "country": self.country,
            "legal_name": self.legal_name,
            "status": self.status,
        }


class Membership(db.Model, TimestampMixin):
    __tablename__ = "memberships"
    __table_args__ = (
        db.UniqueConstraint("enterprise_id", "user_id", name="uq_membership_enterprise_user"),
    )

    id = db.Column(_bigint_pk(), primary_key=True, autoincrement=True)
    enterprise_id = db.Column(db.BigInteger, db.ForeignKey("enterprises.id"), nullable=False)
    user_id = db.Column(db.BigInteger, db.ForeignKey("users.id"), nullable=False, index=True)
    role = db.Column(db.String(16), nullable=False, default="MEMBER")  # OWNER / ADMIN / MEMBER
    status = db.Column(
        db.String(16), nullable=False, default="PENDING"
    )  # PENDING / ACTIVE / REJECTED / REMOVED / LEFT
    approved_via = db.Column(db.String(32), nullable=False, default="")
    decided_at = db.Column(db.DateTime, nullable=True)
    revoked_at = db.Column(db.DateTime, nullable=True)


# ---------- 申请与邀请 ----------


class Application(db.Model, TimestampMixin):
    __tablename__ = "applications"

    id = db.Column(_bigint_pk(), primary_key=True, autoincrement=True)
    type = db.Column(
        db.String(32), nullable=False
    )  # ENTERPRISE_VERIFICATION / MEMBERSHIP
    applicant_user_id = db.Column(
        db.BigInteger, db.ForeignKey("users.id"), nullable=False, index=True
    )
    target_enterprise_id = db.Column(
        db.BigInteger, db.ForeignKey("enterprises.id"), nullable=True
    )
    # 表单快照（企业：legal_name/country/registration_no/contact...；入企：message...）
    form = db.Column(db.JSON, nullable=False)
    status = db.Column(
        db.String(24), nullable=False, default="DRAFT"
    )  # DRAFT/SUBMITTED/UNDER_REVIEW/NEEDS_INFO/APPROVED/REJECTED/WITHDRAWN
    version = db.Column(db.Integer, nullable=False, default=1)  # 乐观并发
    review_note = db.Column(db.String(512), nullable=False, default="")
    submitted_at = db.Column(db.DateTime, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "status": self.status,
            "form": self.form,
            "version": self.version,
            "review_note": self.review_note,
            "target_enterprise_id": self.target_enterprise_id,
            "submitted_at": self.submitted_at.isoformat() if self.submitted_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class Invitation(db.Model, TimestampMixin):
    __tablename__ = "invitations"

    id = db.Column(_bigint_pk(), primary_key=True, autoincrement=True)
    token_hash = db.Column(db.String(64), nullable=False, unique=True)
    enterprise_id = db.Column(db.BigInteger, db.ForeignKey("enterprises.id"), nullable=False)
    created_by_user_id = db.Column(db.BigInteger, db.ForeignKey("users.id"), nullable=False)
    max_uses = db.Column(db.Integer, nullable=False, default=1)
    use_count = db.Column(db.Integer, nullable=False, default=0)
    expires_at = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(16), nullable=False, default="ACTIVE")  # ACTIVE/REVOKED/EXPIRED


# ---------- 文件（含上传会话生命周期） ----------


class File(db.Model, TimestampMixin):
    __tablename__ = "files"

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    workspace_type = db.Column(db.String(16), nullable=False, default="PERSONAL")  # PERSONAL/ENTERPRISE
    enterprise_id = db.Column(db.BigInteger, db.ForeignKey("enterprises.id"), nullable=True)
    created_by_user_id = db.Column(db.BigInteger, db.ForeignKey("users.id"), nullable=False, index=True)
    uploader_user_id = db.Column(db.BigInteger, db.ForeignKey("users.id"), nullable=False)
    purpose = db.Column(db.String(32), nullable=False, default="OCR_TASK")
    original_name = db.Column(db.String(255), nullable=False, default="")
    storage_path = db.Column(db.String(512), nullable=False, default="")
    mime_type = db.Column(db.String(128), nullable=False, default="")
    size_bytes = db.Column(db.BigInteger, nullable=False, default=0)
    page_count = db.Column(db.Integer, nullable=False, default=0)
    sha256 = db.Column(db.String(64), nullable=False, default="")
    status = db.Column(
        db.String(16), nullable=False, default="UPLOADING"
    )  # UPLOADING/UPLOADED/VALIDATING/READY/REJECTED
    reject_reason = db.Column(db.String(255), nullable=False, default="")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "workspace_type": self.workspace_type,
            "enterprise_id": self.enterprise_id,
            "purpose": self.purpose,
            "original_name": self.original_name,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "page_count": self.page_count,
            "sha256": self.sha256,
            "status": self.status,
            "reject_reason": self.reject_reason,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ---------- OCR 任务 ----------


class OcrJob(db.Model, TimestampMixin):
    __tablename__ = "ocr_jobs"

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    workspace_type = db.Column(db.String(16), nullable=False, default="PERSONAL")
    enterprise_id = db.Column(db.BigInteger, db.ForeignKey("enterprises.id"), nullable=True)
    created_by_user_id = db.Column(db.BigInteger, db.ForeignKey("users.id"), nullable=False, index=True)
    file_id = db.Column(db.String(36), db.ForeignKey("files.id"), nullable=False)
    params = db.Column(db.JSON, nullable=False, default=dict)
    capability = db.Column(db.JSON, nullable=False, default=dict)  # TextIn 能力快照
    status = db.Column(
        db.String(24), nullable=False, default="QUEUED", index=True
    )  # QUEUED/PREPROCESSING/PROCESSING/POSTPROCESSING/
    # SUCCEEDED/PARTIAL_SUCCESS/FAILED/CANCEL_REQUESTED/CANCELLED
    idempotency_key = db.Column(db.String(128), nullable=True, unique=True)
    parent_job_id = db.Column(db.String(36), nullable=True)  # rerun 关联
    progress = db.Column(db.Integer, nullable=False, default=0)
    error_code = db.Column(db.String(64), nullable=False, default="")
    error_message = db.Column(db.String(512), nullable=False, default="")
    vendor_request_id = db.Column(db.String(64), nullable=False, default="")
    page_count = db.Column(db.Integer, nullable=False, default=0)
    table_count = db.Column(db.Integer, nullable=False, default=0)
    field_count = db.Column(db.Integer, nullable=False, default=0)
    duration_ms = db.Column(db.BigInteger, nullable=False, default=0)
    missing_pages = db.Column(db.JSON, nullable=True)  # PARTIAL_SUCCESS 缺口
    review_status = db.Column(
        db.String(16), nullable=False, default="UNREVIEWED"
    )  # UNREVIEWED/EDITED/CONFIRMED
    started_at = db.Column(db.DateTime, nullable=True)
    finished_at = db.Column(db.DateTime, nullable=True)

    def to_summary(self, file_name: str = "") -> dict:
        return {
            "id": self.id,
            "workspace_type": self.workspace_type,
            "enterprise_id": self.enterprise_id,
            "file_id": self.file_id,
            "file_name": file_name,
            "status": self.status,
            "progress": self.progress,
            "page_count": self.page_count,
            "table_count": self.table_count,
            "field_count": self.field_count,
            "duration_ms": self.duration_ms,
            "review_status": self.review_status,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "parent_job_id": self.parent_job_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }


# ---------- 结果版本与页结果 ----------


class ResultVersion(db.Model):
    __tablename__ = "result_versions"
    __table_args__ = (
        db.UniqueConstraint("job_id", "version", name="uq_result_version_job_version"),
    )

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    job_id = db.Column(db.String(36), db.ForeignKey("ocr_jobs.id"), nullable=False, index=True)
    version = db.Column(db.Integer, nullable=False, default=1)
    is_current = db.Column(db.Boolean, nullable=False, default=True)
    created_by_user_id = db.Column(db.BigInteger, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_now)


class PageResult(db.Model):
    __tablename__ = "page_results"
    __table_args__ = (
        db.UniqueConstraint("result_version_id", "page_number", name="uq_page_result_version_page"),
    )

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    result_version_id = db.Column(
        db.String(36), db.ForeignKey("result_versions.id"), nullable=False, index=True
    )
    page_number = db.Column(db.Integer, nullable=False)  # 原始页码（1 起）
    text = db.Column(db.Text, nullable=False, default="")
    # [{table_id, row_count, column_count, cells:[{row_index,column_index,row_span,
    #   column_span,raw_text,current_text,polygon,confidence,review_status}]}]
    tables = db.Column(db.JSON, nullable=False, default=list)
    # [{name, value, source, source_page}]
    fields = db.Column(db.JSON, nullable=False, default=list)


# ---------- 导出 ----------


class Export(db.Model, TimestampMixin):
    __tablename__ = "exports"

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    job_id = db.Column(db.String(36), db.ForeignKey("ocr_jobs.id"), nullable=False, index=True)
    result_version_id = db.Column(db.String(36), db.ForeignKey("result_versions.id"), nullable=False)
    format = db.Column(db.String(8), nullable=False)  # XLSX / CSV / TXT
    status = db.Column(
        db.String(16), nullable=False, default="GENERATING"
    )  # GENERATING/READY/EXPIRED/FAILED
    storage_path = db.Column(db.String(512), nullable=False, default="")
    expires_at = db.Column(db.DateTime, nullable=True)
    created_by_user_id = db.Column(db.BigInteger, db.ForeignKey("users.id"), nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "job_id": self.job_id,
            "result_version_id": self.result_version_id,
            "format": self.format,
            "status": self.status,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ---------- 通知 / 审计 / 数据请求 / 外部审核游标 ----------


class Notification(db.Model):
    __tablename__ = "notifications"

    id = db.Column(_bigint_pk(), primary_key=True, autoincrement=True)
    user_id = db.Column(db.BigInteger, db.ForeignKey("users.id"), nullable=False, index=True)
    type = db.Column(db.String(32), nullable=False, default="INFO")
    title = db.Column(db.String(128), nullable=False, default="")
    body = db.Column(db.String(512), nullable=False, default="")
    read_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_now)


class AuditEvent(db.Model):
    __tablename__ = "audit_events"

    id = db.Column(_bigint_pk(), primary_key=True, autoincrement=True)
    user_id = db.Column(db.BigInteger, db.ForeignKey("users.id"), nullable=False, index=True)
    action = db.Column(db.String(64), nullable=False)
    object_type = db.Column(db.String(32), nullable=False, default="")
    object_id = db.Column(db.String(64), nullable=False, default="")
    detail = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime, nullable=False, default=_now)


class DataRequest(db.Model):
    __tablename__ = "data_requests"

    id = db.Column(_bigint_pk(), primary_key=True, autoincrement=True)
    user_id = db.Column(db.BigInteger, db.ForeignKey("users.id"), nullable=False, index=True)
    kind = db.Column(db.String(16), nullable=False)  # ACCESS / DELETION
    status = db.Column(db.String(16), nullable=False, default="PENDING")  # PENDING/DONE/REJECTED
    note = db.Column(db.String(255), nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=_now)
    processed_at = db.Column(db.DateTime, nullable=True)


class ReviewSyncCursor(db.Model):
    """外部审核主动拉取的幂等游标（接口冻结前仅保留结构）。"""

    __tablename__ = "review_sync_cursors"

    id = db.Column(_bigint_pk(), primary_key=True, autoincrement=True)
    source = db.Column(db.String(32), nullable=False, unique=True)
    cursor = db.Column(db.String(128), nullable=False, default="")
    updated_at = db.Column(db.DateTime, nullable=False, default=_now)
