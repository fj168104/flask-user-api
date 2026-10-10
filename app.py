"""Flask 主应用（PRD v1.2 /api/v1 架构）。

- 唯一匿名白名单：POST /api/v1/auth/line（middleware 强制）
- 其余 /api/v1/* 必须携带 HttpOnly 会话 Cookie + CSRF（写操作）
- 未知版本 / 旧版未版本化路径一律 404，无兼容别名
- OCR 由进程内 worker 轮询自有队列表驱动（不依赖厂商回调）

运行：python app.py  （开发；生产用 gunicorn/waitress 等 WSGI 服务器）
"""
from __future__ import annotations

import os

from flask import Flask, jsonify
from werkzeug.exceptions import RequestEntityTooLarge

from config import Config
from errors import ApiError, error_response, invalid_argument
from extensions import db, init_textin
from middleware import register_security
from worker import start_worker


def create_app(config_overrides: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)
    if config_overrides:
        app.config.update(config_overrides)
    app.json.ensure_ascii = False

    db.init_app(app)
    init_textin(app)

    from routes import v1_bp

    app.register_blueprint(v1_bp, url_prefix="/api/v1")

    register_security(app)

    @app.errorhandler(ApiError)
    def _handle_api_error(err: ApiError):
        return error_response(err)

    @app.errorhandler(RequestEntityTooLarge)
    def _request_too_large(_):
        return error_response(invalid_argument("files.too_large"))

    for key in ("FILE_STORAGE_DIR", "EXPORT_STORAGE_DIR", "OCR_STORAGE_DIR"):
        os.makedirs(app.config[key], exist_ok=True)

    with app.app_context():
        db.create_all()

    if app.config.get("OCR_WORKER_ENABLED", True):
        start_worker(app)

    @app.get("/api/health")
    def health():
        client = app.extensions.get("textin_client")
        return jsonify(
            ok=True,
            service="saiflow-api",
            vendor="textin" if client is not None else "unconfigured",
        )

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
