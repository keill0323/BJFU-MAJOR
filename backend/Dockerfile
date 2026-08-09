# 北林 CS2 Major 后端镜像
FROM python:3.12-slim

WORKDIR /app

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 拷贝代码
COPY app ./app
COPY .env.example ./.env.example

EXPOSE 8000

# 生产用 gunicorn 多 worker（uvicorn worker 需安装 gunicorn+uvicorn[standard]）
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
