"""统一错误约定（PRD §14.4）。

业务错误抛出 ApiError，由全局 errorhandler 转为：
    {code, message_key, request_id, retryable, field_errors}
成功响应统一为 ok(data) → {code: "OK", data: ...}。
"""
from __future__ import annotations

import uuid

from typing import Any

from flask import jsonify, request


class ApiError(Exception):
    def __init__(
        self,
        http_status: int,
        code: str,
        message_key: str,
        *,
        retryable: bool = False,
        field_errors: list[dict] | None = None,
    ) -> None:
        super().__init__(message_key)
        self.http_status = http_status
        self.code = code
        self.message_key = message_key
        self.retryable = retryable
        self.field_errors = field_errors or []


def request_id() -> str:
    rid = getattr(request, "_saiflow_request_id", None)
    if not rid:
        rid = uuid.uuid4().hex[:16]
        try:
            request._saiflow_request_id = rid
        except RuntimeError:  # 无请求上下文（如 worker）
            return rid
    return rid


def error_payload(err: ApiError) -> dict[str, Any]:
    return {
        "code": err.code,
        "message_key": err.message_key,
        "request_id": request_id(),
        "retryable": err.retryable,
        "field_errors": err.field_errors,
    }


def error_response(err: ApiError):
    return jsonify(error_payload(err)), err.http_status


def ok(data: Any = None, status: int = 200):
    return jsonify({"code": "OK", "data": data if data is not None else {}}), status


# ---- 常用错误工厂 ----

def auth_required() -> ApiError:
    return ApiError(401, "AUTH_REQUIRED", "auth.login_required")


def session_expired() -> ApiError:
    return ApiError(401, "SESSION_EXPIRED", "auth.session_expired")


def session_revoked() -> ApiError:
    return ApiError(401, "SESSION_REVOKED", "auth.session_revoked")


def account_disabled() -> ApiError:
    return ApiError(403, "ACCOUNT_DISABLED", "auth.account_disabled")


def permission_denied() -> ApiError:
    return ApiError(403, "PERMISSION_DENIED", "error.permission_denied")


def csrf_invalid() -> ApiError:
    return ApiError(403, "CSRF_INVALID", "error.csrf_invalid")


def not_found() -> ApiError:
    return ApiError(404, "RESOURCE_NOT_FOUND", "error.not_found")


def invalid_argument(
    message_key: str = "error.invalid_argument",
    *,
    field_errors: list[dict] | None = None,
) -> ApiError:
    return ApiError(400, "INVALID_ARGUMENT", message_key, field_errors=field_errors)


def state_conflict(message_key: str = "error.state_conflict") -> ApiError:
    return ApiError(409, "STATE_CONFLICT", message_key)


def quota_exceeded() -> ApiError:
    return ApiError(402, "QUOTA_EXCEEDED", "quota.exceeded")


def version_conflict() -> ApiError:
    return ApiError(409, "VERSION_CONFLICT", "error.version_conflict")


def rate_limited() -> ApiError:
    return ApiError(429, "RATE_LIMITED", "error.rate_limited", retryable=True)


def service_unavailable(code: str = "SERVICE_UNAVAILABLE", message_key: str = "error.service_unavailable") -> ApiError:
    return ApiError(503, code, message_key, retryable=True)
