"""
FastAPI 应用入口
Author: keill
Since: 2026-07-21
"""
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.database import engine, Base
from app.config import settings
import app.models
from app.routers import auth, team, match, hall, admin, notifications, recruitment
from contextlib import asynccontextmanager
import threading
from app.services import wechat_service

Base.metadata.create_all(bind=engine)

@asynccontextmanager
async def lifespan(app):
    stop = threading.Event()
    if wechat_service.templates():
        threading.Thread(target=wechat_service.worker, args=(stop,), daemon=True, name="wechat-outbox").start()
    yield
    stop.set()


app = FastAPI(title="北京林业大学CS2校赛报名系统", version="1.0.0", lifespan=lifespan)

# 挂载上传文件目录（学信网截图等），保证图片可通过 /uploads/xxx 访问
_upload_dir = Path(settings.UPLOAD_DIR)
_upload_dir.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(_upload_dir)), name="uploads")

# CORS：开发默认放行全部；生产通过 CORS_ORIGINS 限定域名（逗号分隔）
if settings.CORS_ORIGINS.strip() == "*":
    _origins = ["*"]
else:
    _origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth.router)
app.include_router(team.router)
app.include_router(match.router)
app.include_router(hall.router)
app.include_router(admin.router)
app.include_router(notifications.router)
app.include_router(recruitment.router)


@app.get("/")
def root():
    return {"message": "北京林业大学CS2校赛 API", "version": "1.0.0"}

