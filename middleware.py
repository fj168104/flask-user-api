"""全局安全中间件（PRD §14.3 请求处理顺序）。

- 仅放行精确匹配 POST /api/v1/auth/line；其余 /api/v1 一律校验会话。
- 未知版本（/api/v2/...）与旧无版本 /api 业务路径返回 404，不回落。
- 写请求（POST/PUT/PATCH/DELETE）在有效会话下再校验 CSRF。
- 跨用户/企业对象不存在时统一 404，不泄露资源存在性。
"""
from __future__ import annotations

import re

from flask import Flask, g, request

from auth import raise_for_session_error, resolve_session, verify_csrf
from errors import csrf_invalid, not_found

VERSIONED_API = re.compile(r"^/api/(v\d+)/")
KNOWN_VERSIONS = {"v1"}
# 唯一免登录白名单（PRD §14.1：精确匹配，不做前缀/方法通配）
ANONYMOUS_EXACT = {("POST", "/api/v1/auth/line")}
# 非版本化运维端点（非业务接口）
OPS_EXACT = {"/api/health"}


def register_security(app: Flask) -> None:
    @app.before_request
    def _api_security_gate():
        path = request.path

        # /api 前缀之外直接放行
        if not path.startswith("/api"):
            return None
        if path in OPS_EXACT:
            return None

        m = VERSIONED_API.match(path)
        if m:
            version = m.group(1)
            if version not in KNOWN_VERSIONS:
                raise not_found()  # 未启用版本 404，不回落
            # —— 已启用版本：登录白名单或统一会话校验 ——
            if (request.method, path) in ANONYMOUS_EXACT:
                return None
            session, user, err = resolve_session()
            if err:
                raise_for_session_error(err)
            g.current_session = session
            g.current_user = user
            if request.method in ("POST", "PUT", "PATCH", "DELETE"):
                if not verify_csrf(session):
                    raise csrf_invalid()
            return None

        if path == "/api" or path.startswith("/api/"):
            # 旧无版本业务入口（/api/login、/api/ocr 等）：一律 404，不留别名
            raise not_found()
        return None
