"""Flask 扩展实例（避免循环导入）。"""
from __future__ import annotations

import atexit
import logging
from typing import Any, Mapping

import redis
from flask import Flask
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

# Redis 客户端（延迟初始化，create_app 中 configure）
redis_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    """获取已初始化的 Redis 客户端。"""
    if redis_client is None:
        raise RuntimeError("Redis 尚未初始化，请先调用 init_redis")
    return redis_client


def init_redis(config) -> None:
    """根据 Config 初始化 Redis 连接。config 为 Flask app.config（dict）。"""
    global redis_client
    redis_client = redis.Redis(
        host=config["REDIS_HOST"],
        port=config["REDIS_PORT"],
        password=config["REDIS_PASSWORD"],
        db=config["REDIS_DB"],
        decode_responses=True,
        socket_connect_timeout=5,
    )


# ---------- TextIn OCR provider ----------


def init_textin(app: Flask) -> None:
    """Wire TextIn settings + (optional) HTTP client into a Flask app.

    Stores the settings on ``app.extensions['textin_settings']`` and (when
    credentials are present) a live client on ``app.extensions['textin_client']``.

    The client is intentionally absent when credentials are missing — POST
    /api/v1/ocr/extract then returns 503, while list/detail/file routes
    continue working from local storage.
    """
    # Local import keeps extensions.py importable without httpx/pydantic
    # when the target project is used without the TextIn package.
    from textin.client import TextInClient
    from textin.config import build_textin_settings

    logger = logging.getLogger(__name__)

    settings = build_textin_settings(app.config)
    settings.ocr_storage_dir.mkdir(parents=True, exist_ok=True)
    settings.ocr_uploads_dir.mkdir(parents=True, exist_ok=True)

    app.extensions["textin_settings"] = settings

    if settings.has_credentials():
        client = TextInClient(settings)
        app.extensions["textin_client"] = client

        def _close_client():
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass

        atexit.register(_close_client)
    else:
        logger.warning(
            "TEXTIN_APP_ID / TEXTIN_SECRET_CODE 未配置；/api/v1/ocr/extract 将返回 503。"
        )