"""会话认证（PRD §4.2/§4.3）：HttpOnly Cookie + CSRF。

- 会话令牌只以 SHA-256 摘要入库；Cookie 值永不落库明文。
- CSRF 令牌同样只存摘要，明文仅随登录/session 接口下发。
- 会话有绝对过期（30 天）与空闲过期（12 小时），撤销即置 revoked_at。
"""
from __future__ import annotations

import hashlib
import secrets

from datetime import timedelta
from typing import Any

from flask import current_app, g, request

from errors import session_expired, session_revoked
from extensions import db
from models import Session, User


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def create_session(user: User, *, language: str | None = None) -> tuple[Session, str, str]:
    """创建会话，返回 (session, 明文会话令牌, 明文 CSRF 令牌)。"""
    from datetime import datetime, timezone

    token = secrets.token_urlsafe(48)
    csrf = secrets.token_urlsafe(32)
    idle_ttl = current_app.config["SESSION_IDLE_TTL_SECONDS"]
    absolute_ttl = current_app.config["SESSION_ABSOLUTE_TTL_SECONDS"]
    now_dt = datetime.now(timezone.utc).replace(tzinfo=None)
    session = Session(
        token_hash=_hash(token),
        user_id=user.id,
        csrf_hash=_hash(csrf),
        issued_at=now_dt,
        absolute_expires_at=now_dt + timedelta(seconds=absolute_ttl),
        idle_expires_at=now_dt + timedelta(seconds=idle_ttl),
        created_ip=(request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
                    if request else "") or (request.remote_addr or ""),
    )
    if language:
        user.language = language
    db.session.add(session)
    db.session.flush()
    return session, token, csrf


def resolve_session() -> tuple[Session | None, User | None, str | None]:
    """从 Cookie 解析会话。返回 (session, user, error_code)。"""
    token = request.cookies.get(current_app.config["SESSION_COOKIE_NAME"], "")
    if not token:
        return None, None, "AUTH_REQUIRED"
    session = Session.query.filter_by(token_hash=_hash(token)).first()
    if session is None:
        return None, None, "AUTH_REQUIRED"
    if session.is_revoked:
        return session, None, "SESSION_REVOKED"
    if session.is_expired():
        return session, None, "SESSION_EXPIRED"
    user = db.session.get(User, session.user_id)
    if user is None or user.status != "ACTIVE":
        return session, None, "ACCOUNT_DISABLED"
    # 空闲续期
    session.touch(current_app.config["SESSION_IDLE_TTL_SECONDS"])
    db.session.commit()
    return session, user, None


def verify_csrf(session: Session) -> bool:
    csrf = request.headers.get(current_app.config["CSRF_HEADER_NAME"], "")
    return bool(csrf) and _hash(csrf) == session.csrf_hash


def revoke_session(session: Session) -> None:
    from datetime import datetime, timezone

    if session.revoked_at is None:
        session.revoked_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.session.commit()


def set_session_cookie(response, token: str) -> None:
    response.set_cookie(
        current_app.config["SESSION_COOKIE_NAME"],
        token,
        max_age=current_app.config["SESSION_ABSOLUTE_TTL_SECONDS"],
        httponly=True,
        secure=current_app.config["SESSION_COOKIE_SECURE"],
        samesite=current_app.config["SESSION_COOKIE_SAMESITE"],
        path=current_app.config["SESSION_COOKIE_PATH"],
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie(
        current_app.config["SESSION_COOKIE_NAME"],
        path=current_app.config["SESSION_COOKIE_PATH"],
    )


def raise_for_session_error(error_code: str | None) -> None:
    if error_code == "SESSION_EXPIRED":
        raise session_expired()
    if error_code == "SESSION_REVOKED":
        raise session_revoked()
    from errors import auth_required, account_disabled

    if error_code == "ACCOUNT_DISABLED":
        raise account_disabled()
    raise auth_required()


# ---- 请求上下文辅助 ----

def current_user() -> User:
    return g.current_user


def current_session() -> Session:
    return g.current_session


def audit(action: str, object_type: str = "", object_id: str = "", detail: dict | None = None) -> None:
    """最小审计记录（PRD §14.3 末步）。"""
    user = getattr(g, "current_user", None)
    from models import AuditEvent

    db.session.add(
        AuditEvent(
            user_id=user.id if user else 0,
            action=action,
            object_type=object_type,
            object_id=str(object_id or ""),
            detail=detail or {},
        )
    )
    db.session.commit()


def notify(user_id: int, type_: str, title: str, body: str = "") -> None:
    from models import Notification

    db.session.add(Notification(user_id=user_id, type=type_, title=title, body=body))
    db.session.commit()
