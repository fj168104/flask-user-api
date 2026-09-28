"""Google Cloud Vision OCR 模块（REST API + API Key 方式）。

API Key 从 config.Config.GOOGLE_VISION_API_KEY 读取（来自 .env）。

调用 Vision REST 接口：
    POST https://vision.googleapis.com/v1/images:annotate?key=<API_KEY>
    body: {"requests":[{"image":{"content":"<base64>"},
                        "features":[{"type":"DOCUMENT_TEXT_DETECTION"}]}]}

返回结构（供前端按原图版式渲染）：
    {
        "image_width":  int,
        "image_height": int,
        "full_text":    str,
        "blocks": [
            {"text": str, "vertices": [{"x":int,"y":int},...], "confidence": float},
            ...
        ],
    }
"""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from typing import Any

# Vision REST 端点（API Key 作为 query 参数）
_VISION_URL = "https://vision.googleapis.com/v1/images:annotate"


def _annotate(image_bytes: bytes, api_key: str) -> dict[str, Any]:
    """调用 Vision REST API，返回单个 response（dict）。"""
    content_b64 = base64.b64encode(image_bytes).decode("ascii")
    payload = {
        "requests": [
            {
                "image": {"content": content_b64},
                "features": [{"type": "DOCUMENT_TEXT_DETECTION", "maxResults": 1}],
            }
        ]
    }
    body = json.dumps(payload).encode("utf-8")
    url = f"{_VISION_URL}?key={api_key}"
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # API 返回错误（如 key 无效、配额超限）
        msg = ""
        try:
            err_body = json.loads(e.read().decode("utf-8"))
            msg = err_body.get("error", {}).get("message", "")
        except Exception:  # noqa: BLE001
            msg = e.reason or str(e)
        raise RuntimeError(f"Vision API 调用失败 (HTTP {e.code}): {msg}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"无法连接 Google Vision API: {e.reason}") from e

    responses = data.get("responses", [])
    if not responses:
        raise RuntimeError("Vision API 未返回任何响应")
    return responses[0]


def detect_document(image_bytes: bytes, api_key: str) -> dict[str, Any]:
    """对图片字节做 DOCUMENT_TEXT_DETECTION，返回带边界框的文字块。

    Args:
        image_bytes: 图片二进制。
        api_key: Google Cloud Vision API Key。

    Returns:
        见模块 docstring。无文字时 blocks 为空。
    """
    if not api_key:
        raise RuntimeError(
            "未配置 Google Vision API Key：请在 .env 设置 GOOGLE_VISION_API_KEY"
        )

    resp = _annotate(image_bytes, api_key)

    # API 层面的错误（如非图片格式）
    if "error" in resp:
        err = resp["error"]
        raise RuntimeError(
            f"Vision API 返回错误 (code {err.get('code')}): {err.get('message', '')}"
        )

    annotation = resp.get("fullTextAnnotation") or {}

    blocks: list[dict[str, Any]] = []
    # 优先用 pages[].blocks，它带段落与置信度
    for page in annotation.get("pages", []):
        for block in page.get("blocks", []):
            # 收集块内全部文字与每个 symbol 的置信度
            block_text = ""
            raw_syms: list[tuple[str, float | None]] = []
            confs: list[float] = []
            for paragraph in block.get("paragraphs", []):
                conf = paragraph.get("confidence")
                if conf is not None:
                    confs.append(conf)
                for word in paragraph.get("words", []):
                    for sym in word.get("symbols", []):
                        raw_syms.append((sym.get("text", ""), sym.get("confidence")))
                    # 段落结尾换行
                block_text += "\n"
            # 过滤策略：只丢弃"块开头"的低置信度字符（装饰图标通常在单元格最前面），
            # 且若过滤后文本为空则保留原文，避免误伤正常文字。
            SYM_CONF_THRESHOLD = 0.80
            kept = []
            leading = True
            for ch, sc in raw_syms:
                if leading and sc is not None and sc < SYM_CONF_THRESHOLD:
                    continue  # 跳过开头低置信度字符（装饰图标）
                leading = False
                kept.append(ch)
            filtered = "".join(kept).strip()
            block_text = filtered if filtered else block_text.rstrip("\n").strip()
            if not block_text:
                continue
            bp = block.get("boundingBox", {})
            vertices = [
                {"x": v.get("x", 0), "y": v.get("y", 0)}
                for v in bp.get("vertices", [])
            ]
            confidence = (sum(confs) / len(confs)) if confs else 0.0
            blocks.append(
                {
                    "text": block_text,
                    "vertices": vertices,
                    "confidence": round(confidence, 4),
                }
            )

    # 图片尺寸：从所有 vertices 极值近似
    image_width = 0
    image_height = 0
    all_x: list[int] = []
    all_y: list[int] = []
    for ta in resp.get("textAnnotations", []):
        for v in ta.get("boundingPoly", {}).get("vertices", []):
            if v.get("x") is not None:
                all_x.append(v["x"])
            if v.get("y") is not None:
                all_y.append(v["y"])
    if all_x:
        image_width = max(all_x)
    if all_y:
        image_height = max(all_y)

    return {
        "image_width": image_width,
        "image_height": image_height,
        "full_text": annotation.get("text", ""),
        "blocks": blocks,
    }
