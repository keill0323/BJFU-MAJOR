"""
FastAPI 应用入口
Author: keill
Since: 2026-07-21
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.database import engine, Base
import app.models
from app.routers import auth, team, match

Base.metadata.create_all(bind=engine)

app = FastAPI(title="北京林业大学CS2校赛报名系统", version="1.0.0")

# CORS 配置，允许小程序和网页后台访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth.router)
app.include_router(team.router)
app.include_router(match.router)


@app.get("/")
def root():
    return {"message": "北京林业大学CS2校赛 API", "version": "1.0.0"}

