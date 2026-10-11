"""入企申请 / 邀请（PRD §5、§14.2）。

公司本身的添加/修改/编号绑定/解绑在 routes/companies.py；企业认证走
后台人工审核 enterprises.status，不再走 Application 工单。
- 入企申请：针对 VERIFIED 企业；申请即建 PENDING 成员行，审核通过转 ACTIVE。
- 邀请：OWNER/ADMIN 生成一次性令牌（明文只回显一次，库存哈希）；
  兑换需登录态，绝不提供匿名回调。
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from flask import Blueprint, current_app, request

from auth import audit, current_user, notify
from errors import (
    invalid_argument,
    not_found,
    ok,
    permission_denied,
    state_conflict,
)
from extensions import db
from models import Application, Enterprise, Invitation, Membership, _now

applications_bp = Blueprint("applications", __name__)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _member_role(user_id: int, enterprise_id: int) -> str | None:
    m = Membership.query.filter_by(enterprise_id=enterprise_id, user_id=user_id).first()
    return m.role if m else None


def _require_admin(user_id: int, enterprise_id: int) -> Membership:
    m = Membership.query.filter_by(enterprise_id=enterprise_id, user_id=user_id).first()
    if m is None or m.status != "ACTIVE" or m.role not in ("OWNER", "ADMIN"):
        raise permission_denied()
    return m


# ---------- 入企申请 ----------


@applications_bp.post("/enterprises/<int:enterprise_id>/membership-applications")
def submit_membership_application(enterprise_id: int):
    user = current_user()
    e = db.session.get(Enterprise, enterprise_id)
    if e is None or e.status != "VERIFIED":
        raise not_found()

    body = request.get_json(silent=True) or {}
    message = str(body.get("message") or "").strip()
    if len(message) > 500:
        raise invalid_argument(field_errors=[{"field": "message", "reason": "too_long"}])

    m = Membership.query.filter_by(enterprise_id=enterprise_id, user_id=user.id).first()
    if m is not None and m.status in ("PENDING", "ACTIVE"):
        raise state_conflict("application.duplicate_open")
    if m is not None and m.status in ("REJECTED", "REMOVED"):
        raise state_conflict("membership.rejoin_forbidden")

    app_row = Application(
        type="MEMBERSHIP",
        applicant_user_id=user.id,
        target_enterprise_id=enterprise_id,
        form={"message": message, "enterprise_code": e.code},
        status="SUBMITTED",
        submitted_at=_now(),
    )
    db.session.add(app_row)
    db.session.flush()
    if m is None:
        db.session.add(
            Membership(
                enterprise_id=enterprise_id,
                user_id=user.id,
                role="MEMBER",
                status="PENDING",
                approved_via="APPLICATION",
            )
        )
    else:  # LEFT：重新申请回到 PENDING
        m.status = "PENDING"
        m.approved_via = "APPLICATION"
        m.decided_at = None
    db.session.commit()
    audit("application.submit", "application", str(app_row.id), {"type": "MEMBERSHIP"})
    return ok(app_row.to_dict(), status=201)


# ---------- 邀请 ----------


@applications_bp.post("/enterprises/<int:enterprise_id>/invitations")
def create_invitation(enterprise_id: int):
    user = current_user()
    e = db.session.get(Enterprise, enterprise_id)
    if e is None:
        raise not_found()
    _require_admin(user.id, enterprise_id)

    body = request.get_json(silent=True) or {}
    max_uses = body.get("max_uses", 1)
    ttl_hours = body.get("ttl_hours", 72)
    if not isinstance(max_uses, int) or isinstance(max_uses, bool) or not 1 <= max_uses <= 100:
        raise invalid_argument(field_errors=[{"field": "max_uses", "reason": "invalid"}])
    if not isinstance(ttl_hours, int) or isinstance(ttl_hours, bool) or not 1 <= ttl_hours <= 24 * 30:
        raise invalid_argument(field_errors=[{"field": "ttl_hours", "reason": "invalid"}])

    token = secrets.token_urlsafe(24)
    inv = Invitation(
        token_hash=_hash_token(token),
        enterprise_id=enterprise_id,
        created_by_user_id=user.id,
        max_uses=max_uses,
        expires_at=_now() + timedelta(hours=ttl_hours),
        status="ACTIVE",
    )
    db.session.add(inv)
    db.session.commit()
    audit("invitation.create", "invitation", str(inv.id), {"enterprise_id": enterprise_id})
    base = current_app.config.get("APP_BASE_URL", "")
    return ok(
        {
            "id": inv.id,
            "token": token,  # 明文仅此一次
            "url": f"{base}/join?token={token}" if base else None,
            "expires_at": inv.expires_at.isoformat(),
            "max_uses": inv.max_uses,
        },
        status=201,
    )


@applications_bp.post("/invitations/redeem")
def redeem_invitation():
    user = current_user()
    body = request.get_json(silent=True) or {}
    token = body.get("token") if isinstance(body, dict) else None
    if not token or not isinstance(token, str) or len(token) > 128:
        raise invalid_argument(field_errors=[{"field": "token", "reason": "required"}])

    inv = Invitation.query.filter_by(token_hash=_hash_token(token.strip())).first()
    if inv is None or inv.status != "ACTIVE":
        raise not_found()  # 不区分不存在/失效，防枚举
    if inv.expires_at <= _now():
        inv.status = "EXPIRED"
        db.session.commit()
        raise state_conflict("invitation.expired")
    if inv.use_count >= inv.max_uses:
        inv.status = "EXPIRED"
        db.session.commit()
        raise state_conflict("invitation.exhausted")

    e = db.session.get(Enterprise, inv.enterprise_id)
    if e is None or e.status != "VERIFIED":
        raise state_conflict("invitation.enterprise_unavailable")

    m = Membership.query.filter_by(enterprise_id=inv.enterprise_id, user_id=user.id).first()
    if m is not None and m.status in ("ACTIVE", "PENDING"):
        raise state_conflict("membership.already_member")
    if m is not None and m.status in ("REJECTED", "REMOVED"):
        raise state_conflict("membership.rejoin_forbidden")

    if m is None:
        m = Membership(
            enterprise_id=inv.enterprise_id,
            user_id=user.id,
            role="MEMBER",
            status="ACTIVE",
            approved_via="INVITATION",
            decided_at=_now(),
        )
        db.session.add(m)
    else:  # LEFT
        m.status = "ACTIVE"
        m.approved_via = "INVITATION"
        m.decided_at = _now()
        m.revoked_at = None

    inv.use_count += 1
    if inv.use_count >= inv.max_uses:
        inv.status = "EXPIRED"
    db.session.commit()
    audit("invitation.redeem", "invitation", str(inv.id), {"enterprise_id": inv.enterprise_id})
    notify(user.id, "MEMBERSHIP_ACTIVE", "Workspace joined", e.legal_name)
    return ok({"enterprise": e.to_dict(), "role": m.role}, status=200)


@applications_bp.delete("/invitations/<int:invitation_id>")
def revoke_invitation(invitation_id: int):
    user = current_user()
    inv = db.session.get(Invitation, invitation_id)
    if inv is None:
        raise not_found()
    role = _member_role(user.id, inv.enterprise_id)
    if inv.created_by_user_id != user.id and role not in ("OWNER", "ADMIN"):
        raise permission_denied()
    if inv.status != "ACTIVE":
        raise state_conflict("invitation.not_active")
    inv.status = "REVOKED"
    db.session.commit()
    audit("invitation.revoke", "invitation", str(inv.id))
    return ok({"id": inv.id, "status": "REVOKED"})
