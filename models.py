"""数据模型。"""
from datetime import datetime
from extensions import db


class User(db.Model):
    """用户表。"""

    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def set_password(self, password: str) -> None:
        """生成密码哈希。"""
        from werkzeug.security import generate_password_hash

        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        """校验密码。"""
        from werkzeug.security import check_password_hash

        return check_password_hash(self.password_hash, password)

    def to_dict(self) -> dict:
        """序列化为字典（不含密码）。"""
        return {
            "id": self.id,
            "username": self.username,
            "email": self.email,
            "created_at": self.created_at.isoformat(),
        }
