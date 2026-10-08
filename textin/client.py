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

    @property
    def endpoint(self) -> str:
        return self._settings.textin_endpoint

    @property
    def extract_endpoint(self) -> str:
        return self._settings.textin_extract_endpoint

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

    def close(self) -> None:
        self._client.close()

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