"""共享测试 fixture：构造仅含 TextIn provider 的最小 Flask 应用，避免触碰 MySQL/Redis。"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from flask import Flask

from textin.client import TextInClient
from textin.config import TextInSettings, build_textin_settings
from textin.errors import OcrHTTPError
from textin.routes import ocr_bp


# 与源码 test_ocr.py 中 mock_textin_response 等价的响应；
# 路由层不关心 data 提升，client 层在 _parse 中已展开。
@pytest.fixture
def mock_textin_payload() -> dict:
    return {
        "code": 200,
        "message": "success",
        "elements": [
            {
                "element_id": "e1",
                "type": "text",
                "page_number": 1,
                "text": "Invoice #12345",
                "coordinates": [0, 0, 100, 0, 100, 20, 0, 20],
            },
            {
                "element_id": "e2",
                "type": "text",
                "page_number": 1,
                "text": "Total: 1000.00",
                "coordinates": [0, 30, 100, 30, 100, 50, 0, 50],
            },
            {
                "element_id": "e3",
                "type": "text",
                "page_number": 2,
                "text": "Page 2 content",
                "coordinates": [0, 0, 50, 0, 50, 20, 0, 20],
            },
        ],
        "metadata": {"page_count": 2, "filename": "sample.png", "filetype": "image/png"},
        "pages": [{"page_number": 1}, {"page_number": 2}],
        "markdown": "# Invoice\n发票号: INV-1",
    }


@pytest.fixture
def textin_app(tmp_path: Path, mock_textin_payload: dict) -> Flask:
    """Minimal Flask app with only the TextIn blueprint; no MySQL/Redis/extensions."""
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        TEXTIN_APP_ID="test_app_id",
        TEXTIN_SECRET_CODE="test_secret_code",
        OCR_STORAGE_DIR=str(tmp_path / "extractions"),
        OCR_UPLOADS_DIR=str(tmp_path / "uploads"),
        MAX_UPLOAD_BYTES=20 * 1024 * 1024,
        TEXTIN_ENDPOINT="https://api.textin.com/api/v1/xparse/parse/sync",
        TEXTIN_EXTRACT_ENDPOINT="https://api.textin.com/api/xparse/extract/sync",
    )
    app.json.ensure_ascii = False

    settings = build_textin_settings(app.config)
    (tmp_path / "extractions").mkdir(parents=True, exist_ok=True)
    (tmp_path / "uploads").mkdir(parents=True, exist_ok=True)
    app.extensions["textin_settings"] = settings

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=mock_textin_payload)

    app.extensions["textin_client"] = TextInClient(settings, transport=httpx.MockTransport(handler))

    app.register_blueprint(ocr_bp, url_prefix="/api/v1/ocr")
    return app


@pytest.fixture
def client(textin_app: Flask):
    return textin_app.test_client()