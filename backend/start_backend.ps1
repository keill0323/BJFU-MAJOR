# 一键启动后端（必须在 backend 目录启动，否则读不到 .env！）
# 用法：右键此文件 -> 使用 PowerShell 运行
Set-Location "D:\vs code 库\北林major报名系统\backend"
& "D:\vs code 库\北林major报名系统\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8000
