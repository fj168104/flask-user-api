"""文件对象访问控制：本人范围 + 企业 ACTIVE 成员（PRD §14.2/§15）。

跨用户/企业资源一律 404，不泄露存在性。
"""
from __future__ import annotations

from errors import not_found
from extensions import db
from models import File, Membership, User


def get_file_for_user(file_id: str, user: User) -> File:
    f = db.session.get(File, file_id)
    if f is None:
        raise not_found()
    if f.created_by_user_id == user.id:
        return f
    if f.workspace_type == "ENTERPRISE" and f.enterprise_id:
        m = Membership.query.filter_by(
            enterprise_id=f.enterprise_id, user_id=user.id, status="ACTIVE"
        ).first()
        if m is not None:
            return f
    raise not_found()
