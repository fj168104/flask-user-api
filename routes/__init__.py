"""聚合 /api/v1 业务蓝图（PRD §14：统一错误信封）。"""
from __future__ import annotations

from flask import Blueprint

from errors import ApiError, error_response
from routes.applications import applications_bp
from routes.auth import bp as auth_bp
from routes.exports import exports_bp
from routes.files import bp as files_bp
from routes.me import bp as me_bp
from routes.ocr import ocr_bp
from routes.results import results_bp

v1_bp = Blueprint("api_v1", __name__)


@v1_bp.errorhandler(ApiError)
def _handle_api_error(err: ApiError):
    return error_response(err)


for bp in (auth_bp, me_bp, files_bp, ocr_bp, results_bp, exports_bp, applications_bp):
    v1_bp.register_blueprint(bp)
