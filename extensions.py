"""Flask 扩展实例（避免循环导入）。"""
from __future__ import annotations

import atexit
import logging

from flask import Flask
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def init_textin(app: Flask) -> None:
    """装配 TextIn 配置与客户端（唯一 OCR 厂商，PRD §12）。

    未配置凭据时不创建 client，OCR 任务会进入 FAILED(能力不可用)；
    其余路由不受影响。
    """
    from textin.client import TextInClient
    from textin.config import build_textin_settings

    logger = logging.getLogger(__name__)

    settings = build_textin_settings(app.config)
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
        logger.warning("TEXTIN_APP_ID / TEXTIN_SECRET_CODE 未配置；OCR 任务将失败。")
