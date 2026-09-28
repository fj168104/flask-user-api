"""token 生成、校验、销毁（基于 Redis）。"""
import secrets

from extensions import get_redis
from models import User


def _key(token: str) -> str:
    """拼接 Redis 键。"""
    from flask import current_app

    prefix = current_app.config["TOKEN_KEY_PREFIX"]
    return f"{prefix}{token}"


def generate_token(user: User) -> str:
    """为用户生成随机 token 并写入 Redis（值为 user_id），返回 token。"""
    from flask import current_app

    token = secrets.token_urlsafe(32)
    get_redis().setex(
        _key(token),
        current_app.config["TOKEN_EXPIRE_SECONDS"],
        user.id,
    )
    return token


def resolve_user_from_token(token: str) -> "User | None":
    """根据 token 从 Redis 取 user_id，再查库返回用户；token 无效或用户不存在返回 None。"""
    if not token:
        return None
    raw = get_redis().get(_key(token))
    if raw is None:
        return None
    try:
        user_id = int(raw)
    except (TypeError, ValueError):
        return None
    from extensions import db

    return db.session.get(User, user_id)


def revoke_token(token: str) -> None:
    """销毁 token（退出登录）。"""
    if token:
        get_redis().delete(_key(token))
