"""应用配置（PRD v1.2 /api/v1 架构）。"""
import os

from urllib.parse import quote_plus

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, "true" if default else "false").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "change-this-in-production")

    # MySQL 数据库连接（Tencent Cloud CynosDB）
    MYSQL_HOST = "gz-cynosdbmysql-grp-g82l5jpt.sql.tencentcdb.com"
    MYSQL_PORT = 25349
    MYSQL_USER = "root"
    MYSQL_PASSWORD = "T48m.j{]5xJ!Qp"
    MYSQL_DB = "sai-flow"

    SQLALCHEMY_DATABASE_URI = (
        f"mysql+pymysql://{MYSQL_USER}:{quote_plus(MYSQL_PASSWORD)}"
        f"@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DB}?charset=utf8mb4"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # 让 jsonify 直接输出 UTF-8 中文，而非 \uXXXX 转义
    JSON_ENSURE_ASCII = False

    # ---------- 会话（PRD §4.2：HttpOnly Cookie + CSRF） ----------
    SESSION_COOKIE_NAME = "saiflow_session"
    # 绝对过期：30 天；空闲过期：12 小时
    SESSION_ABSOLUTE_TTL_SECONDS = 30 * 24 * 3600
    SESSION_IDLE_TTL_SECONDS = 12 * 3600
    # Secure cookie 仅经 env 开启（本地 http 开发置 false；生产必须 true）
    SESSION_COOKIE_SECURE = _bool("SESSION_COOKIE_SECURE", False)
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_PATH = "/api"
    CSRF_HEADER_NAME = "X-CSRF-Token"

    # ---------- LINE 登录 ----------
    LINE_CHANNEL_ID = os.getenv("LINE_CHANNEL_ID", "")
    # 开发兜底：未配置 LINE 渠道时允许 dev_id 登录（仅限本地开发，生产必须关闭）
    AUTH_DEV_MODE = _bool("AUTH_DEV_MODE", False)

    # ---------- 上传与任务限额（PRD §11） ----------
    # 对象存储后端（storage.py）：local=本地盘（开发/测试），firebase=Firebase Storage（生产）
    STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "local")
    GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "saiflow-509205")
    FIREBASE_STORAGE_BUCKET = os.getenv(
        "FIREBASE_STORAGE_BUCKET", "saiflow-509205.firebasestorage.app"
    )
    FILE_STORAGE_DIR = os.getenv("FILE_STORAGE_DIR", "./storage/files")
    EXPORT_STORAGE_DIR = os.getenv("EXPORT_STORAGE_DIR", "./storage/exports")
    ALLOWED_FILE_EXTENSIONS = {"jpg", "jpeg", "png", "pdf"}
    ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png"}
    MAX_CONTENT_LENGTH = 20 * 1024 * 1024  # 单文件 20MB（上传 content 走 PUT 流，仅兜底）
    MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024  # 单文件 20MB
    MAX_IMAGES_PER_TASK = 10
    MAX_PAGES_PER_TASK = 50
    MAX_IMAGES_TOTAL_BYTES = 50 * 1024 * 1024  # 图片合计 50MB
    UPLOAD_SESSION_TTL_SECONDS = 24 * 3600

    # ---------- TextIn OCR provider（唯一厂商，PRD §12） ----------
    TEXTIN_APP_ID = os.getenv("TEXTIN_APP_ID", "4a8a32165b164e4fef6f2e54336de2d7")
    TEXTIN_SECRET_CODE = os.getenv("TEXTIN_SECRET_CODE", "eac1b93270d3c7b9b7641c89309bb444")
    TEXTIN_ENDPOINT = os.getenv(
        "TEXTIN_ENDPOINT", "https://api.textin.com/api/v1/xparse/parse/sync"
    )
    TEXTIN_EXTRACT_ENDPOINT = os.getenv(
        "TEXTIN_EXTRACT_ENDPOINT", "https://api.textin.com/api/v1/xparse/extract/sync"
    )
    OCR_STORAGE_DIR = os.getenv("OCR_STORAGE_DIR", "./temp/extractions")
    TEXTIN_TIMEOUT_SECONDS = float(os.getenv("TEXTIN_TIMEOUT_SECONDS", "120"))
    TEXTIN_CONNECT_TIMEOUT_SECONDS = float(os.getenv("TEXTIN_CONNECT_TIMEOUT_SECONDS", "10"))
