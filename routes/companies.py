"""公司管理：添加/修改、按编号绑定/解绑、人工审核（PRD §5 变体，按需求定稿）。

字段对应：编号=enterprises.id（自增生成）· 名称=legal_name · 公司代码=registration_no
· 证明材料照片=cert_file_id→files（purpose=APPLICATION_MATERIAL）· 联系人手机号=contact_phone。

审核：添加/修改后 status=PENDING_VERIFICATION；后台人工把 enterprises.status
改成 VERIFIED 才生效（绑定与工作空间均要求 VERIFIED）。审核不通过设 DISABLED。
"""
from __future__ import annotations

import secrets

from flask import Blueprint, request

from auth import audit, current_user, notify
from errors import invalid_argument, not_found, ok, permission_denied, state_conflict
from extensions import db
from models import Enterprise, File, Membership, _now

companies_bp = Blueprint("companies", __name__)


def _parse_company_payload(body: dict) -> dict:
    legal_name = str(body.get("legal_name") or "").strip()
    registration_no = str(body.get("registration_no") or "").strip()
    contact_phone = str(body.get("contact_phone") or "").strip()
    field_errors = []
    if not 1 <= len(legal_name) <= 255:
        field_errors.append({"field": "legal_name", "reason": "required"})
    if not 1 <= len(registration_no) <= 64:
        field_errors.append({"field": "registration_no", "reason": "required"})
    if len(contact_phone) > 32:
        field_errors.append({"field": "contact_phone", "reason": "invalid"})
    if field_errors:
        raise invalid_argument(field_errors=field_errors)
    return {
        "legal_name": legal_name,
        "registration_no": registration_no,
        "contact_phone": contact_phone,
    }


def _validate_cert_file(cert_file_id, user_id: int) -> str | None:
    """证明材料必须是本人上传、用途 APPLICATION_MATERIAL 且 READY 的文件。"""
    if cert_file_id in (None, ""):
        return None
    f = db.session.get(File, str(cert_file_id))
    if (
        f is None
        or f.created_by_user_id != user_id
        or f.purpose != "APPLICATION_MATERIAL"
        or f.status != "READY"
    ):
        raise invalid_argument(field_errors=[{"field": "cert_file_id", "reason": "invalid"}])
    return f.id


def _require_owner(user_id: int, enterprise_id: int) -> Membership:
    m = Membership.query.filter_by(enterprise_id=enterprise_id, user_id=user_id).first()
    if m is None or m.status != "ACTIVE" or m.role != "OWNER":
        raise permission_denied()
    return m


@companies_bp.post("/enterprises")
def create_company():
    """添加公司：待审核（PENDING_VERIFICATION），审核通过才生效。"""
    user = current_user()
    body = request.get_json(silent=True) or {}
    data = _parse_company_payload(body)
    cert_file_id = _validate_cert_file(body.get("cert_file_id"), user.id)

    enterprise = Enterprise(
        code=f"ent-{_now().strftime('%y%m%d')}-{secrets.token_hex(3)}",
        legal_name=data["legal_name"],
        registration_no=data["registration_no"],
        contact_phone=data["contact_phone"],
        cert_file_id=cert_file_id,
        status="PENDING_VERIFICATION",
    )
    db.session.add(enterprise)
    db.session.flush()
    # 添加人即 OWNER；所有访问仍要求企业 VERIFIED，未过审前不影响任何人
    db.session.add(
        Membership(
            enterprise_id=enterprise.id,
            user_id=user.id,
            role="OWNER",
            status="ACTIVE",
            approved_via="CREATE",
            decided_at=_now(),
        )
    )
    db.session.commit()
    audit("company.create", "enterprise", str(enterprise.id))
    return ok({"enterprise": enterprise.to_dict()}, status=201)


@companies_bp.get("/enterprises/mine")
def my_companies():
    """我的公司（创建或被绑定，含待审核）：编号/名称/状态 + 我的角色。"""
    user = current_user()
    rows = (
        db.session.query(Membership, Enterprise)
        .join(Enterprise, Membership.enterprise_id == Enterprise.id)
        .filter(
            Membership.user_id == user.id,
            Membership.status.in_(("PENDING", "ACTIVE")),
        )
        .order_by(Enterprise.created_at.desc())
        .all()
    )
    items = [
        {**e.to_dict(), "my_role": m.role, "membership_status": m.status}
        for m, e in rows
    ]
    return ok({"items": items})


@companies_bp.patch("/enterprises/<int:enterprise_id>")
def update_company(enterprise_id: int):
    """修改公司（仅 ACTIVE OWNER）。已生效公司被修改后回到待审核。"""
    user = current_user()
    e = db.session.get(Enterprise, enterprise_id)
    if e is None:
        raise not_found()
    _require_owner(user.id, enterprise_id)

    body = request.get_json(silent=True) or {}
    data = _parse_company_payload(body)
    cert_file_id = _validate_cert_file(body.get("cert_file_id"), user.id)

    e.legal_name = data["legal_name"]
    e.registration_no = data["registration_no"]
    e.contact_phone = data["contact_phone"]
    if cert_file_id:
        e.cert_file_id = cert_file_id
    if e.status == "VERIFIED":
        e.status = "PENDING_VERIFICATION"  # 关键信息变更，需重新审核
    db.session.commit()
    audit("company.update", "enterprise", str(e.id))
    return ok({"enterprise": e.to_dict()})


@companies_bp.post("/enterprises/bind")
def bind_company():
    """按公司代码绑定已生效公司；一人可绑定多个，解绑后可重新绑定。"""
    user = current_user()
    body = request.get_json(silent=True) or {}
    code = str(body.get("code") or "").strip()
    if not code:
        raise invalid_argument(field_errors=[{"field": "code", "reason": "required"}])

    e = Enterprise.query.filter(Enterprise.code == code).first()
    if e is None or e.status != "VERIFIED":
        raise not_found()  # 不存在或未过审，不区分

    m = Membership.query.filter_by(enterprise_id=e.id, user_id=user.id).first()
    if m is not None and m.status in ("ACTIVE", "PENDING"):
        raise state_conflict("membership.already_member")
    if m is not None and m.status in ("REJECTED", "REMOVED"):
        raise state_conflict("membership.rejoin_forbidden")

    if m is None:
        m = Membership(
            enterprise_id=e.id,
            user_id=user.id,
            role="MEMBER",
            status="ACTIVE",
            approved_via="BIND",
            decided_at=_now(),
        )
        db.session.add(m)
    else:  # LEFT → 重新绑定
        m.status = "ACTIVE"
        m.approved_via = "BIND"
        m.decided_at = _now()
        m.revoked_at = None
    db.session.commit()
    audit("company.bind", "enterprise", str(e.id))
    notify(user.id, "MEMBERSHIP_ACTIVE", "Workspace joined", e.legal_name)
    return ok({"enterprise": e.to_dict(), "role": m.role})


@companies_bp.post("/enterprises/<int:enterprise_id>/unbind")
def unbind_company(enterprise_id: int):
    """解绑公司（解绑后不消失于历史，但不再是有效身份）。"""
    user = current_user()
    m = Membership.query.filter_by(enterprise_id=enterprise_id, user_id=user.id).first()
    if m is None or m.status not in ("ACTIVE", "PENDING"):
        raise not_found()
    m.status = "LEFT"
    m.revoked_at = _now()
    db.session.commit()
    audit("company.unbind", "enterprise", str(enterprise_id))
    return ok({"enterprise_id": enterprise_id, "status": "LEFT"})
