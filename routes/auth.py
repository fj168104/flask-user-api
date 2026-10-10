"""认证路由：POST /api/v1/auth/line、GET /api/v1/auth/session、POST /api/v1/auth/logout。

PRD §14.1：/api/v1/auth/line 是全系统唯一免登录白名单；
但仍必须服务端验证 LINE ID token（签名/签发者/过期/渠道受众）。
未配置 LINE 渠道时可用 AUTH_DEV_MODE=1 开启本地开发兜底（生产禁用）。
"""
from __future__ import annotations

import hashlib
import logging
import secrets

from datetime import datetime, timezone

import httpx
from flask import Blueprint, current_app, g, request

from auth import (
    clear_session_cookie,
    create_session,
    current_session,
    current_user,
    revoke_session,
    set_session_cookie,
)
from errors import ApiError, invalid_argument, ok, service_unavailable
from extensions import db
from models import Session, User
from workspace import list_workspaces

logger = logging.getLogger(__name__)
bp = Blueprint("auth", __name__)

LINE_VERIFY_URL = "https://api.line.me/oauth2/v2.1/verify"
LINE_ISSUER = "https://access.line.me"


def _issue_session_response(user: User, language: str | None = None):
    session, token, csrf = create_session(user, language=language)
    db.session.commit()
    payload = {
        "user": user.to_dict(),
        "csrf_token": csrf,
        "workspaces": list_workspaces(user),
    }
    resp = ok(payload)
    set_session_cookie(resp[0], token)
    return resp


def _rotate_csrf(session: Session) -> str:
    csrf = secrets.token_urlsafe(32)
    session.csrf_hash = hashlib.sha256(csrf.encode("utf-8")).hexdigest()
    db.session.commit()
    return csrf


def _upsert_line_user(provider: str, sub: str, display_name: str, avatar_url: str, language: str | None) -> User:
    user = User.query.filter_by(line_provider=provider, line_sub=sub).first()
    if user is None:
        user = User(
            line_provider=provider,
            line_sub=sub,
            display_name=display_name or sub,
            avatar_url=avatar_url or "",
            language=language or "th",
        )
        db.session.add(user)
        db.session.flush()
    else:
        if display_name:
            user.display_name = display_name
        if avatar_url:
            user.avatar_url = avatar_url
        if language in ("th", "en"):
            user.language = language
    return user


def _verify_line_id_token(id_token: str) -> dict:
    """调用 LINE 服务端验真，并本地复核 iss/exp/aud（PRD §15）。"""
    channel_id = current_app.config["LINE_CHANNEL_ID"]
    if not channel_id:
        raise service_unavailable(
            "AUTH_SERVICE_UNAVAILABLE", "auth.line_not_configured"
        )
    try:
        resp = httpx.get(
            LINE_VERIFY_URL,
            params={"id_token": id_token, "client_id": channel_id},
            timeout=httpx.Timeout(10.0, connect=5.0),
        )
    except httpx.HTTPError as e:
        logger.warning("LINE verify 网络失败: %s", e)
        raise service_unavailable(
            "AUTH_SERVICE_UNAVAILABLE", "auth.line_verify_unavailable"
        )
    if resp.status_code != 200:
        raise ApiError(401, "AUTH_REQUIRED", "auth.line_token_invalid")
    claims = resp.json()
    now = int(datetime.now(timezone.utc).timestamp())
    if claims.get("iss") != LINE_ISSUER:
        raise ApiError(401, "AUTH_REQUIRED", "auth.line_token_invalid")
    aud = claims.get("aud")
    if aud != channel_id and channel_id not in (
        aud if isinstance(aud, list) else [aud]
    ):
        raise ApiError(401, "AUTH_REQUIRED", "auth.line_channel_mismatch")
    if int(claims.get("exp", 0)) < now:
        raise ApiError(401, "AUTH_REQUIRED", "auth.line_token_expired")
    return claims


@bp.post("/auth/line")
def auth_line():
    """LINE ID token 换取 saiflow 会话（唯一匿名白名单接口）。"""
    body = request.get_json(silent=True) or {}
    id_token = (body.get("id_token") or "").strip()
    language = body.get("language") if body.get("language") in ("th", "en") else None

    if not id_token:
        # 开发兜底：未配置 LINE 渠道时允许 dev_id 建立开发身份（生产禁用）
        dev_id = (body.get("dev_id") or "").strip()
        if dev_id and current_app.config["AUTH_DEV_MODE"]:
            if len(dev_id) > 64:
                raise invalid_argument("error.invalid_argument")
            user = _upsert_line_user("line", f"dev:{dev_id}", dev_id, "", language)
            return _issue_session_response(user, language)
        raise invalid_argument("auth.id_token_required")

    claims = _verify_line_id_token(id_token)
    user = _upsert_line_user(
        "line",
        str(claims.get("sub")),
        str(claims.get("name") or ""),
        str(claims.get("picture") or ""),
        language,
    )
    return _issue_session_response(user, language)


@bp.get("/auth/session")
def auth_session():
    """当前会话摘要 + 轮换下发 CSRF token（需登录态，本身不在白名单）。"""
    session = current_session()
    user = current_user()
    csrf = _rotate_csrf(session)
    return ok(
        {
            "user": user.to_dict(),
            "csrf_token": csrf,
            "workspaces": list_workspaces(user),
            "issued_at": session.issued_at.isoformat() if session.issued_at else None,
            "absolute_expires_at": (
                session.absolute_expires_at.isoformat() if session.absolute_expires_at else None
            ),
        }
    )


@bp.post("/auth/logout")
def auth_logout():
    session = current_session()
    revoke_session(session)
    resp = ok({"logged_out": True})
    clear_session_cookie(resp[0])
    return resp
