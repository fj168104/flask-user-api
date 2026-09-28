"""Flask 扩展实例（避免循环导入）。"""
import redis
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

# Redis 客户端（延迟初始化，create_app 中 configure）
redis_client: "redis.Redis | None" = None


def get_redis() -> "redis.Redis":
    """获取已初始化的 Redis 客户端。"""
    if redis_client is None:
        raise RuntimeError("Redis 尚未初始化，请先调用 init_redis")
    return redis_client


def init_redis(config) -> None:
    """根据 Config 初始化 Redis 连接。config 为 Flask app.config（dict）。"""
    global redis_client
    redis_client = redis.Redis(
        host=config["REDIS_HOST"],
        port=config["REDIS_PORT"],
        password=config["REDIS_PASSWORD"],
        db=config["REDIS_DB"],
        decode_responses=True,
        socket_connect_timeout=5,
    )
