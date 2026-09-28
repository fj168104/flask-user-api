# Flask 用户服务镜像
FROM python:3.10-slim

# 设置时区，避免日志时间错乱
ENV TZ=Asia/Shanghai

# 工作目录
WORKDIR /app

# 先装依赖（利用 Docker 层缓存：代码变动不会重新装包）
# 用腾讯云内网镜像加速（也可换清华 https://pypi.tuna.tsinghua.edu.cn/simple）
COPY requirements.txt .
RUN pip install --no-cache-dir \
    -i https://mirrors.cloud.tencent.com/pypi/simple \
    --timeout 120 --retries 5 \
    -r requirements.txt

# 复制应用代码
COPY app.py config.py extensions.py models.py auth.py ocr.py ./

# 暴露端口
EXPOSE 5000

# 上传图片持久化目录（运行时挂载 volume）
RUN mkdir -p /app/uploads
VOLUME ["/app/uploads"]

# 用 gunicorn 生产级 WSGI 启动
# app:app => 模块 app.py 中的 Flask 实例 app
# --workers 2 多进程，--bind 监听 0.0.0.0:5000
CMD ["gunicorn", "--workers", "2", "--bind", "0.0.0.0:5000", "--access-logfile", "-", "--error-logfile", "-", "app:app"]
