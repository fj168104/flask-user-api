"""对象存储抽象：上传原件 / 页预览 / 导出文件 / 审计副本的字节落地。

双后端，`STORAGE_BACKEND` 切换（config.py）：
- `local`（默认，开发/测试）：字节落本地盘，storage_path 存绝对路径；
- `firebase`（生产）：firebase_admin Storage，storage_path 存对象名
  （`files/<id>.<ext>`、`exports/<id>.<fmt>`、`audit/<job_id>.json`、
  `previews/<file_id>/page_<n>.png`）。

下载/预览一律经后端代理（send_file(BytesIO) + 会话鉴权），不生成任何匿名或签名 URL。
firebase_admin 惰性初始化：只有真正读写对象时才初始化，本地开发与测试无需 GCP 凭证。
"""
from __future__ import annotations

import logging
from pathlib import Path

from flask import current_app

logger = logging.getLogger(__name__)

# category → 本地目录配置键 / 本地子目录前缀 / firebase 对象前缀
_DIR_KEY = {
    "files": "FILE_STORAGE_DIR",
    "exports": "EXPORT_STORAGE_DIR",
    "audit": "OCR_STORAGE_DIR",
    "previews": "FILE_STORAGE_DIR",
}
_LOCAL_PREFIX = {"previews": "_previews"}
_FIRE_PREFIX = {
    "files": "files",
    "exports": "exports",
    "audit": "audit",
    "previews": "previews",
}

_FB: dict = {"bucket": None, "failed": False}


def backend() -> str:
    return str(current_app.config.get("STORAGE_BACKEND", "local"))


def stored_path(category: str, name: str) -> str:
    """计算对象落点（不写数据）：local=绝对路径，firebase=对象名。"""
    if backend() == "firebase":
        return f"{_FIRE_PREFIX[category]}/{name}"
    prefix = _LOCAL_PREFIX.get(category, "")
    return str(Path(current_app.config[_DIR_KEY[category]]) / prefix / name)


def put_bytes(
    category: str, name: str, data: bytes, content_type: str = "application/octet-stream"
) -> str:
    """存字节，返回 storage_path（local=绝对路径，firebase=对象名）。"""
    path = stored_path(category, name)
    if backend() == "firebase":
        _bucket().blob(path).upload_from_string(data, content_type=content_type)
        return path
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return path


def get_bytes(storage_path: str) -> bytes:
    if backend() == "firebase":
        return _bucket().blob(storage_path).download_as_bytes()
    return Path(storage_path).read_bytes()


def exists(storage_path: str) -> bool:
    if not storage_path:
        return False
    if backend() == "firebase":
        return _bucket().blob(storage_path).exists()
    return Path(storage_path).exists()


def delete(storage_path: str) -> None:
    """尽力删除，对象已不存在视为成功。"""
    if not storage_path:
        return
    try:
        if backend() == "firebase":
            _bucket().blob(storage_path).delete()
        else:
            Path(storage_path).unlink(missing_ok=True)
    except Exception:  # noqa: BLE001 清理失败不阻断业务流
        logger.warning("storage delete failed: %s", storage_path)


def _bucket():
    if _FB["failed"]:
        raise RuntimeError("firebase storage unavailable")
    if _FB["bucket"] is None:
        try:
            import firebase_admin
            from firebase_admin import credentials, storage

            cred = credentials.ApplicationDefault()
            firebase_admin.initialize_app(
                cred,
                {
                    "projectId": current_app.config.get(
                        "GOOGLE_CLOUD_PROJECT", "saiflow-509205"
                    ),
                    "storageBucket": current_app.config["FIREBASE_STORAGE_BUCKET"],
                },
            )
            _FB["bucket"] = storage.bucket()
        except Exception as e:  # noqa: BLE001
            _FB["failed"] = True
            logger.exception("firebase storage init failed")
            raise RuntimeError("firebase storage unavailable") from e
    return _FB["bucket"]
