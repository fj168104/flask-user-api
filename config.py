"""应用配置"""
import os

from urllib.parse import quote_plus

from dotenv import load_dotenv

load_dotenv()


class Config:
    # 密钥，用于 session 签名与 CSRF 保护
    SECRET_KEY = os.getenv("SECRET_KEY", "change-this-in-production")

    # Google Cloud Vision API Key（用于 OCR）
    GOOGLE_VISION_API_KEY = os.getenv("GOOGLE_VISION_API_KEY", "")

    # MySQL 数据库连接（Tencent Cloud CynosDB）
    # 密码含特殊字符，使用 quote_plus 编码
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

    # Redis 缓存（用于存储登录 token）
    REDIS_HOST = "182.254.156.80"
    REDIS_PORT = 6379
    REDIS_PASSWORD = "Snowbb661"
    REDIS_DB = 1

    # token 有效期（秒），7 天
    TOKEN_EXPIRE_SECONDS = 7 * 24 * 3600
    # Redis 中 token 键前缀
    TOKEN_KEY_PREFIX = "token:"

    # 让 jsonify 直接输出 UTF-8 中文，而非 \uXXXX 转义
    JSON_ENSURE_ASCII = False

    # OCR 上传图片保存目录（相对应用根目录或绝对路径）
    UPLOAD_FOLDER = "uploads"
    # 允许的图片扩展名
    ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "bmp", "tiff"}
    # 单文件最大 20MB（同时覆盖 Google Vision /api/ocr 与 TextIn /api/v1/ocr/extract）
    MAX_CONTENT_LENGTH = 20 * 1024 * 1024

    # ---------- TextIn OCR provider (/api/v1/ocr/*) ----------
    TEXTIN_APP_ID = os.getenv("TEXTIN_APP_ID", "")
    TEXTIN_SECRET_CODE = os.getenv("TEXTIN_SECRET_CODE", "")
    TEXTIN_ENDPOINT = os.getenv(
        "TEXTIN_ENDPOINT", "https://api.textin.com/api/v1/xparse/parse/sync"
    )
    TEXTIN_EXTRACT_ENDPOINT = os.getenv(
        "TEXTIN_EXTRACT_ENDPOINT", "https://api.textin.com/api/xparse/extract/sync"
    )
    OCR_STORAGE_DIR = os.getenv("OCR_STORAGE_DIR", "./temp/extractions")
    OCR_UPLOADS_DIR = os.getenv("OCR_UPLOADS_DIR", "./temp/uploads")
    TEXTIN_TIMEOUT_SECONDS = float(os.getenv("TEXTIN_TIMEOUT_SECONDS", "60"))
    TEXTIN_CONNECT_TIMEOUT_SECONDS = float(os.getenv("TEXTIN_CONNECT_TIMEOUT_SECONDS", "10"))
