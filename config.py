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
    # 单文件最大 10MB
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024
