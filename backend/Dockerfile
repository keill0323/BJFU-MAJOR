# 北林 CS2 Major 后端镜像
FROM python:3.12-slim

WORKDIR /app

# 安装依赖（腾讯云 PyPI 镜像，国内服务器加速）
COPY requirements.txt .
RUN pip install --no-cache-dir -i https://mirrors.cloud.tencent.com/pypi/simple -r requirements.txt

# 拷贝代码
COPY app ./app
COPY .env.example ./.env.example

EXPOSE 8000

# 私聊票据与在线提示在单进程内；扩为多实例前必须增加共享广播与票据存储。
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--ws-max-size", "1024", "--ws-max-queue", "4"]
