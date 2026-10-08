"""Flask 主应用：注册 / 登录 / 用户信息 / OCR 识别接口。

鉴权方式：登录返回 token，后续请求在 header 携带：
    Authorization: Bearer <token>

运行：
    pip install -r requirements.txt
    python app.py

接口：
    POST /register      注册
    POST /login         登录，返回 token
    GET  /user          查看当前用户信息（需 token）
    POST /logout        退出登录（需 token）
    POST /ocr           上传图片做 OCR 识别（需 token，multipart/form-data）
    GET  /uploads/<fn>  访问上传的图片
"""
import os
import uuid

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.exceptions import RequestEntityTooLarge
from config import Config
from extensions import db, init_redis, get_redis, init_textin
from models import User
from auth import generate_token, resolve_user_from_token, revoke_token

# Authorization header 中 token 的前缀
BEARER_PREFIX = "Bearer "


def extract_token() -> str:
    """从 Authorization header 提取 token。"""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith(BEARER_PREFIX):
        return auth_header[len(BEARER_PREFIX):].strip()
    return ""


def current_user():
    """从请求头解析 token 并返回 User；失败返回 None。"""
    token = extract_token()
    if not token:
        return None
    return resolve_user_from_token(token)


def create_app() -> Flask:
    """应用工厂。"""
    app = Flask(__name__)
    app.config.from_object(Config)

    # jsonify 直接输出 UTF-8 中文，而非 \uXXXX 转义
    app.json.ensure_ascii = False

    db.init_app(app)
    init_redis(app.config)
    init_textin(app)

    # 注册 TextIn OCR provider（/api/v1/ocr/*；无鉴权，独立存储）
    from textin.routes import ocr_bp

    app.register_blueprint(ocr_bp, url_prefix="/api/v1/ocr")

    # 全局 413：超过 MAX_CONTENT_LENGTH 时统一返回 {code,msg,data} 信封
    @app.errorhandler(RequestEntityTooLarge)
    def _request_too_large(_):
        max_bytes = app.config.get("MAX_CONTENT_LENGTH", 0)
        return (
            jsonify(code=413, msg=f"文件超过 {max_bytes} 字节上限", data=None),
            413,
        )

    # 确保上传目录存在
    upload_folder = app.config["UPLOAD_FOLDER"]
    os.makedirs(upload_folder, exist_ok=True)

    with app.app_context():
        db.create_all()
        # 探测 Redis 连通性，失败快速报错
        try:
            get_redis().ping()
        except Exception as e:  # noqa: BLE001
            app.logger.warning("Redis 连接失败：%s", e)
    
    @app.get("/api/health")
    def health():
        return jsonify(ok=True, service="python-api")
    
    @app.post("/api/register")
    def register():
        data = request.get_json(silent=True) or {}
        username = data.get("username", "").strip()
        email = data.get("email", "").strip()
        password = data.get("password", "")

        if not username or not email or not password:
            return jsonify(code=400, msg="username、email、password 均不能为空"), 400

        if User.query.filter_by(username=username).first():
            return jsonify(code=409, msg="用户名已被占用"), 409
        if User.query.filter_by(email=email).first():
            return jsonify(code=409, msg="邮箱已被注册"), 409

        user = User(username=username, email=email)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        return jsonify(code=201, msg="注册成功", data=user.to_dict()), 201

    @app.post("/api/login")
    def login():
        data = request.get_json(silent=True) or {}
        account = data.get("account", "").strip()
        password = data.get("password", "")

        if not account or not password:
            return jsonify(code=400, msg="account、password 不能为空"), 400

        # account 支持用户名或邮箱登录
        user = (
            User.query.filter_by(username=account).first()
            or User.query.filter_by(email=account).first()
        )
        if user is None or not user.check_password(password):
            return jsonify(code=401, msg="账号或密码错误"), 401

        token = generate_token(user)
        return jsonify(
            code=200,
            msg="登录成功",
            data={
                "token": token,
                "user": user.to_dict(),
            },
        )

    @app.get("/api/user")
    def user_info():
        token = extract_token()
        user = resolve_user_from_token(token)
        if user is None:
            return jsonify(code=401, msg="token 无效或已过期，请重新登录"), 401

        return jsonify(code=200, msg="ok", data=user.to_dict())

    @app.post("/api/logout")
    def logout():
        token = extract_token()
        if not token:
            return jsonify(code=401, msg="缺少 token"), 401
        revoke_token(token)
        return jsonify(code=200, msg="已退出登录")

    @app.post("/api/ocr")
    def ocr_recognize():
        # 需要登录
        user = current_user()
        if user is None:
            return jsonify(code=401, msg="token 无效或已过期，请重新登录"), 401

        # 接收上传文件
        if "file" not in request.files:
            return jsonify(code=400, msg="请上传图片（表单字段名 file）"), 400
        file = request.files["file"]
        if not file or not file.filename:
            return jsonify(code=400, msg="文件名为空"), 400

        # 校验扩展名
        ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
        allowed = app.config["ALLOWED_IMAGE_EXTENSIONS"]
        if ext not in allowed:
            return jsonify(
                code=400,
                msg=f"不支持的图片格式 .{ext}，仅支持 {', '.join(sorted(allowed))}",
            ), 400

        # 保存到 uploads/（用 uuid 防重名与路径穿越）
        filename = f"{uuid.uuid4().hex}.{ext}"
        save_path = os.path.join(upload_folder, filename)
        file.save(save_path)

        # 调 Vision API（REST + API Key）
        try:
            from ocr import detect_document

            with open(save_path, "rb") as f:
                image_bytes = f.read()
            result = detect_document(image_bytes, app.config["GOOGLE_VISION_API_KEY"])
        except RuntimeError as e:
            # 凭据未配置 / Vision 调用失败
            return jsonify(code=503, msg=f"OCR 识别失败：{e}"), 503
        except Exception as e:  # noqa: BLE001
            app.logger.exception("OCR 处理异常")
            return jsonify(code=500, msg=f"服务器内部错误：{e}"), 500

        return jsonify(
            code=200,
            msg="识别成功",
            data={
                "image_url": f"/uploads/{filename}",
                "image_width": result["image_width"],
                "image_height": result["image_height"],
                "full_text": result["full_text"],
                "blocks": result["blocks"],
            },
        )

    @app.get("/api/uploads/<path:filename>")
    def serve_upload(filename):
        """提供上传图片的访问。"""
        return send_from_directory(
            os.path.abspath(upload_folder), filename
        )

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
