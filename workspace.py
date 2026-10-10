"""工作空间解析（PRD §4.1）：个人空间 + 已授权企业空间。"""
from __future__ import annotations

from errors import permission_denied
from extensions import db
from models import Enterprise, Membership, User


def resolve_workspace(
    user: User, workspace_type: str | None, enterprise_id: int | None
) -> tuple[str, int | None]:
    """校验并归一化工作空间。默认个人空间；企业空间必须是 ACTIVE 成员。"""
    ws_type = (workspace_type or "PERSONAL").upper()
    if ws_type not in ("PERSONAL", "ENTERPRISE"):
        from errors import invalid_argument

        raise invalid_argument("error.invalid_workspace")
    if ws_type == "PERSONAL":
        return "PERSONAL", None
    if not enterprise_id:
        from errors import invalid_argument

        raise invalid_argument("error.invalid_workspace")
    membership = Membership.query.filter_by(
        enterprise_id=enterprise_id, user_id=user.id
    ).first()
    enterprise = db.session.get(Enterprise, enterprise_id)
    cross_user_404 = permission_denied()  # 统一 403/404 由调用面上层决定；默认 403
    if membership is None or enterprise is None:
        raise cross_user_404
    if membership.status != "ACTIVE":
        from errors import ApiError

        raise ApiError(403, "ENTERPRISE_ACCESS_REVOKED", "error.enterprise_access_revoked")
    if enterprise.status != "VERIFIED":
        from errors import ApiError

        raise ApiError(403, "ENTERPRISE_ACCESS_REVOKED", "error.enterprise_access_revoked")
    return "ENTERPRISE", enterprise_id


def list_workspaces(user: User) -> list[dict]:
    """当前有效身份列表（个人 + ACTIVE 成员企业）。"""
    out = [
        {
            "workspace_type": "PERSONAL",
            "enterprise_id": None,
            "enterprise_name": None,
            "role": None,
        }
    ]
    rows = (
        Membership.query.filter_by(user_id=user.id, status="ACTIVE")
        .order_by(Membership.created_at.asc())
        .all()
    )
    for m in rows:
        e = db.session.get(Enterprise, m.enterprise_id)
        if e is None or e.status != "VERIFIED":
            continue
        out.append(
            {
                "workspace_type": "ENTERPRISE",
                "enterprise_id": e.id,
                "enterprise_name": e.legal_name,
                "role": m.role,
            }
        )
    return out
