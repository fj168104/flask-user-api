"""个人域路由：/api/v1/me/*（PRD §14.2）。"""
from __future__ import annotations

from flask import Blueprint, request

from auth import audit, current_user
from errors import invalid_argument, ok
from extensions import db
import quota
from models import (
    Application,
    AuditEvent,
    DataRequest,
    Notification,
)
from workspace import list_workspaces

bp = Blueprint("me", __name__)


@bp.get("/me")
def me_get():
    return ok(current_user().to_dict())


@bp.get("/me/quota")
def me_quota():
    """当前有效工作空间的配额视图（PRD v1.4 §20：赠送/消耗/剩余）。"""
    return ok({"workspaces": quota.quota_summary(current_user())})


@bp.patch("/me")
def me_patch():
    """字段白名单：仅 display_name / language（PRD §14.2）。"""
    user = current_user()
    body = request.get_json(silent=True) or {}
    changed = False
    if "display_name" in body:
        name = str(body["display_name"]).strip()
        if not name or len(name) > 128:
            raise invalid_argument(
                "error.invalid_argument",
                field_errors=[{"field": "display_name", "message_key": "error.field_required"}],
            )
        user.display_name = name
        changed = True
    if "language" in body:
        if body["language"] not in ("th", "en"):
            raise invalid_argument(
                "error.invalid_argument",
                field_errors=[{"field": "language", "message_key": "error.invalid_argument"}],
            )
        user.language = body["language"]
        changed = True
    if changed:
        db.session.commit()
        audit("me.update", "user", str(user.id))
    return ok(user.to_dict())


@bp.get("/me/workspaces")
def me_workspaces():
    user = current_user()
    pending = (
        Application.query.filter(
            Application.applicant_user_id == user.id,
            Application.status.in_(["DRAFT", "SUBMITTED", "UNDER_REVIEW", "NEEDS_INFO"]),
        )
        .order_by(Application.updated_at.desc())
        .all()
    )
    return ok(
        {
            "workspaces": list_workspaces(user),
            "pending_applications": [
                {"id": a.id, "type": a.type, "status": a.status} for a in pending
            ],
        }
    )


@bp.get("/me/applications")
def me_applications():
    rows = (
        Application.query.filter_by(applicant_user_id=current_user().id)
        .order_by(Application.updated_at.desc())
        .all()
    )
    return ok({"items": [a.to_dict() for a in rows]})


@bp.get("/me/notifications")
def me_notifications():
    rows = (
        Notification.query.filter_by(user_id=current_user().id)
        .order_by(Notification.created_at.desc())
        .limit(50)
        .all()
    )
    return ok(
        {
            "items": [
                {
                    "id": n.id,
                    "type": n.type,
                    "title": n.title,
                    "body": n.body,
                    "read": n.read_at is not None,
                    "created_at": n.created_at.isoformat() if n.created_at else None,
                }
                for n in rows
            ]
        }
    )


@bp.get("/me/activity-events")
def me_activity_events():
    rows = (
        AuditEvent.query.filter_by(user_id=current_user().id)
        .order_by(AuditEvent.created_at.desc())
        .limit(50)
        .all()
    )
    return ok(
        {
            "items": [
                {
                    "action": e.action,
                    "object_type": e.object_type,
                    "object_id": e.object_id,
                    "created_at": e.created_at.isoformat() if e.created_at else None,
                }
                for e in rows
            ]
        }
    )


@bp.post("/me/data-requests")
def me_data_requests():
    """数据访问/删除请求：需单独确认（confirmed=true 明确发起）。"""
    body = request.get_json(silent=True) or {}
    kind = body.get("kind")
    if kind not in ("ACCESS", "DELETION"):
        raise invalid_argument(
            "error.invalid_argument",
            field_errors=[{"field": "kind", "message_key": "error.invalid_argument"}],
        )
    if body.get("confirmed") is not True:
        raise invalid_argument("data_request.confirm_required")
    req = DataRequest(
        user_id=current_user().id,
        kind=kind,
        note=str(body.get("note") or "")[:255],
    )
    db.session.add(req)
    db.session.commit()
    audit("data_request.create", "data_request", str(req.id), {"kind": kind})
    return ok(
        {
            "id": req.id,
            "kind": req.kind,
            "status": req.status,
            "created_at": req.created_at.isoformat() if req.created_at else None,
        },
        status=201,
    )
