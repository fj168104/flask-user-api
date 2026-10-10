"""TextIn provider settings, loaded from Flask app.config.

Settings are constructed explicitly via build_textin_settings(app.config)
during create_app(); no module-level singleton or filesystem mutation at
import time.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class TextInSettings(BaseSettings):
    """Pydantic-settings model; the Flask factory builds instances manually."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    textin_app_id: str = Field(default="", description="TextIn 应用 ID，传给 x-ti-app-id")
    textin_secret_code: str = Field(default="", description="TextIn 密钥，传给 x-ti-secret-code")
    textin_endpoint: str = "https://api.textin.com/api/v1/xparse/parse/sync"
    textin_extract_endpoint: str = "https://api.textin.com/api/xparse/extract/sync"
    ocr_storage_dir: Path = Path("./temp/extractions")
    ocr_uploads_dir: Path = Path("./temp/uploads")
    max_upload_bytes: int = 20 * 1024 * 1024
    timeout_seconds: float = 60.0
    connect_timeout_seconds: float = 10.0

    def has_credentials(self) -> bool:
        return bool(self.textin_app_id and self.textin_secret_code)


def _resolve(value: Any, default: Any) -> Any:
    """Read value from mapping (case-insensitive), fall back to env, then default."""
    if isinstance(value, Mapping):
        for k in (value if isinstance(value, dict) else dict(value).keys()):
            pass
    if isinstance(value, Mapping):
        for key, val in value.items():
            if isinstance(key, str) and key.lower() == default.__class__.__name__.lower():
                return val
    return value


def build_textin_settings(app_config: Mapping[str, Any]) -> TextInSettings:
    """Build validated settings from a Flask app.config-like mapping.

    Reads keys with the TEXTIN_* / OCR_* names exactly as the Flask Config
    class exposes them, and falls back to environment variables via
    pydantic-settings' BaseSettings default loading.
    """
    data: dict[str, Any] = {}

    candidates = {
        "textin_app_id": ("TEXTIN_APP_ID", "4a8a32165b164e4fef6f2e54336de2d7"),
        "textin_secret_code": ("TEXTIN_SECRET_CODE", "eac1b93270d3c7b9b7641c89309bb444"),
        "textin_endpoint": ("TEXTIN_ENDPOINT", "https://api.textin.com/api/v1/xparse/parse/sync"),
        "textin_extract_endpoint": (
            "TEXTIN_EXTRACT_ENDPOINT",
            "https://api.textin.com/api/xparse/extract/sync",
        ),
        "ocr_storage_dir": ("OCR_STORAGE_DIR", "./temp/extractions"),
        "ocr_uploads_dir": ("OCR_UPLOADS_DIR", "./temp/uploads"),
        "max_upload_bytes": ("MAX_UPLOAD_BYTES", 20 * 1024 * 1024),
        "timeout_seconds": ("TEXTIN_TIMEOUT_SECONDS", 60.0),
        "connect_timeout_seconds": ("TEXTIN_CONNECT_TIMEOUT_SECONDS", 10.0),
    }
    for field_name, (key, default) in candidates.items():
        raw: Any = None
        for k in (key, key.lower()):
            if k in app_config:
                raw = app_config[k]
                break
        if raw is None:
            raw = os.getenv(key)
        if raw is None:
            raw = default
        if field_name in {"ocr_storage_dir", "ocr_uploads_dir"} and not isinstance(raw, Path):
            raw = Path(raw)
        if field_name in {"max_upload_bytes", "timeout_seconds", "connect_timeout_seconds"}:
            raw = type(default)(raw)
        data[field_name] = raw

    return TextInSettings(**data)
