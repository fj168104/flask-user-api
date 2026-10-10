"""TextIn xparse/parse/sync 接口客户端封装。

参考文档：https://docs.textin.com/api-reference/endpoint/xparse/v1/parse-sync
- endpoint: POST https://api.textin.com/api/v1/xparse/parse/sync
- 鉴权 header: x-ti-app-id / x-ti-secret-code
- 请求体 multipart/form-data，必填字段：
    - file (二进制) 或 file_url (字符串) 二选一
    - config (JSON 字符串)，含 capabilities / scope / config.engine_params
- 响应：{"code":200,"data":{...elements, markdown, metadata, pages, ...},"message":"..."}
  错误响应使用 message 字段

Schema 驱动智能抽取：
- endpoint: POST https://api.textin.com/api/xparse/extract/sync
- multipart: file + extract_config(schema) + parse_config
- 响应：{"data":{"result":{"extracted_schema":{name: value | {value,bounding_regions}}, ...}}}

异步解析（适合大文件 / 批处理）：
- submit:   POST https://api.textin.com/api/v1/xparse/parse/async
- status:   GET  https://api.textin.com/api/v1/xparse/parse/async/{job_id}
- result:   GET  <pre-signed result_url>（无鉴权头，由 TextIn 直接返回完整解析结果，
            结构与 /parse/sync 一致）。
- 响应 status: pending | in_progress | completed | failed
- completed 时 result_url 在 data.result_url 中。
"""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from textin.config import TextInSettings
from textin.errors import TextInAPIError

logger = logging.getLogger(__name__)


def build_config(params: dict[str, Any] | None) -> str:
    """把我们的扁平参数映射为 TextIn parse/sync 的 config JSON 字符串。

    用户参数语义 → TextIn 字段：
      parse_mode → config.engine_params.parse_mode     （scan/layout/article）
      table      → capabilities.include_table_structure  （bool）
      formula    → config.engine_params.formula_level    （0/1/2）
      rotate     → capabilities.crop_dewarp              （bool）
      page       → scope.page_range 起始
      page_count → scope.page_range 终止
    """
    p = params or {}
    engine_params: dict[str, Any] = {}
    if p.get("parse_mode"):
        engine_params["parse_mode"] = p["parse_mode"]
    if p.get("formula") is not None:
        engine_params["formula_level"] = 1 if p["formula"] else 0

    capabilities: dict[str, Any] = {
        "include_table_structure": bool(p.get("table", False)),
        "crop_dewarp": bool(p.get("rotate", False)),
    }

    scope: dict[str, Any] = {}
    page = p.get("page")
    page_count = p.get("page_count")
    if page is not None or page_count is not None:
        start = (page or 0) + 1  # TextIn 从 1 开始计数
        end = start + (page_count - 1) if page_count else start
        scope["page_range"] = f"{start}-{end}"

    cfg: dict[str, Any] = {"capabilities": capabilities}
    if scope:
        cfg["scope"] = scope
    if engine_params:
        cfg.setdefault("config", {})["engine_params"] = engine_params
    return json.dumps(cfg, ensure_ascii=False)


def build_extract_schema(field_names: list[str]) -> dict[str, Any]:
    """把用户声明的字段名列表构造成 JSON-Schema：所有字段类型为 string。"""
    return {
        "type": "object",
        "properties": {name: {"type": "string"} for name in field_names if name},
    }


def _extract_value(entry: Any) -> str:
    """extract/sync 响应中 extracted_schema 的值可能是简单字符串，也可能是 {value, bounding_regions}。"""
    if isinstance(entry, dict):
        v = entry.get("value")
        return "" if v is None else str(v)
    if entry is None:
        return ""
    return str(entry)


def parse_extracted_schema(
    extracted_schema: dict[str, Any] | None,
    *,
    page_number: int = 0,
) -> list[dict[str, Any]]:
    """把 extracted_schema 转成 [{page_number, name, value, source='schema'}, ...]；跳过空值。"""
    if not extracted_schema:
        return []
    out: list[dict[str, Any]] = []
    for name, entry in extracted_schema.items():
        value = _extract_value(entry).strip()
        if not value:
            continue
        out.append(
            {
                "page_number": page_number,
                "name": str(name),
                "value": value,
                "source": "schema",
            }
        )
    return out


class TextInClient:
    """TextIn xparse 同步客户端（Flask/WSGI 兼容）。

    Lifecycle is owned by extensions.init_textin: one instance per app.
    Tests can inject a custom `transport` (httpx.MockTransport).
    """

    def __init__(
        self,
        settings: TextInSettings,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._client = httpx.Client(
            transport=transport,
            timeout=httpx.Timeout(
                settings.timeout_seconds,
                connect=settings.connect_timeout_seconds,
            ),
            headers={
                "x-ti-app-id": settings.textin_app_id,
                "x-ti-secret-code": settings.textin_secret_code,
            },
        )
        # 用于下载 pre-signed result_url：不带 x-ti-* 头（避免把凭据发到 TextIn 之外的 CDN 域）
        # 共用同一 transport，方便测试时统一 mock
        self._anon_client = httpx.Client(
            transport=transport,
            timeout=httpx.Timeout(
                settings.timeout_seconds,
                connect=settings.connect_timeout_seconds,
            ),
        )

    @property
    def endpoint(self) -> str:
        return self._settings.textin_endpoint

    @property
    def extract_endpoint(self) -> str:
        return self._settings.textin_extract_endpoint

    @property
    def async_endpoint(self) -> str:
        return self._settings.textin_async_endpoint

    def async_status_endpoint(self, job_id: str) -> str:
        return self._settings.textin_async_status_endpoint.format(job_id=job_id)

    def extract_file(
        self,
        filename: str,
        content: bytes,
        content_type: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """上传本地文件并解析。"""
        config_str = build_config(params)
        form = {
            "file": (filename, content, content_type),
            "config": (None, config_str, "application/json"),
        }
        logger.info(
            "TextIn extract_file: filename=%s content_type=%s size=%d config=%s",
            filename,
            content_type,
            len(content),
            config_str,
        )
        resp = self._client.post(self.endpoint, files=form)
        return self._parse(resp)

    def extract_url(
        self,
        url: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """提交公网可访问的 URL 并解析。"""
        config_str = build_config(params)
        form = {
            "file_url": (None, url, "text/plain"),
            "config": (None, config_str, "application/json"),
        }
        logger.info("TextIn extract_url: url=%s config=%s", url, config_str)
        resp = self._client.post(self.endpoint, files=form)
        return self._parse(resp)

    def extract_with_schema(
        self,
        *,
        filename: str,
        content: bytes,
        content_type: str,
        schema_fields: list[str],
        parse_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """调用 TextIn /api/xparse/extract/sync，按 schema_fields 抽取字段。"""
        schema = build_extract_schema(schema_fields)
        extract_config = {
            "schema": schema,
            "generate_citations": True,
            "stamp": False,
        }
        parse_config = build_config(parse_params)
        form = {
            "file": (filename, content, content_type),
            "extract_config": (
                None,
                json.dumps(extract_config, ensure_ascii=False),
                "application/json",
            ),
            "parse_config": (None, parse_config, "application/json"),
        }
        logger.info(
            "TextIn extract_with_schema: filename=%s fields=%s",
            filename,
            schema_fields,
        )
        resp = self._client.post(self.extract_endpoint, files=form)
        return self._parse(resp)

    def submit_async(
        self,
        filename: str,
        content: bytes,
        content_type: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """提交异步解析任务：POST /xparse/parse/async。

        返回 TextIn 原始响应（已 _parse 解开 data 提升），其中 job_id 在顶层。
        """
        config_str = build_config(params)
        form = {
            "file": (filename, content, content_type),
            "config": (None, config_str, "application/json"),
        }
        logger.info(
            "TextIn submit_async: filename=%s content_type=%s size=%d config=%s",
            filename,
            content_type,
            len(content),
            config_str,
        )
        resp = self._client.post(self.async_endpoint, files=form)
        return self._parse(resp)

    def get_async_status(self, job_id: str) -> dict[str, Any]:
        """查询异步任务状态：GET /xparse/parse/async/{job_id}。

        返回解开 data 提升后的 dict，字段：job_id, file_id?, status, result_url?, message?
        """
        url = self.async_status_endpoint(job_id)
        logger.info("TextIn get_async_status: job_id=%s", job_id)
        resp = self._client.get(url)
        return self._parse(resp)

    def fetch_async_result(self, result_url: str) -> dict[str, Any]:
        """下载预签名的 result_url（无鉴权头）。响应结构与 /parse/sync 相同。"""
        logger.info("TextIn fetch_async_result: url=%s", result_url)
        resp = self._anon_client.get(result_url)
        try:
            payload = resp.json()
        except ValueError as e:
            raise TextInAPIError(-1, f"非 JSON 响应: {resp.text[:200]}") from e
        code = payload.get("code", -1)
        if code != 200:
            raise TextInAPIError(code, payload.get("message", "unknown"))
        if "data" in payload and isinstance(payload["data"], dict):
            return {"code": code, **payload["data"], "message": payload.get("message", "")}
        return payload

    def close(self) -> None:
        self._client.close()
        self._anon_client.close()

    @staticmethod
    def _parse(resp: httpx.Response) -> dict[str, Any]:
        try:
            payload = resp.json()
        except ValueError as e:
            raise TextInAPIError(-1, f"非 JSON 响应: {resp.text[:200]}") from e
        code = payload.get("code", -1)
        if code != 200:
            raise TextInAPIError(code, payload.get("message", "unknown"))
        # 把 data 提升到顶层，方便前端统一访问
        if "data" in payload and isinstance(payload["data"], dict):
            return {"code": code, **payload["data"], "message": payload.get("message", "")}
        return payload