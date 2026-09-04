"""
通用上传服务
Author: keill
Since: 2026-08

统一处理「用户上传图片」的校验与落盘，避免在每个上传接口里重复写
「类型校验 + 大小校验 + 拼文件名 + 写文件」这套逻辑。
"""
import time
from pathlib import Path

from fastapi import HTTPException, UploadFile

from app.config import settings

# 允许的图片类型 → 落盘扩展名
_ALLOWED = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


def save_upload_image(file: UploadFile, prefix: str, max_mb: int = 10) -> tuple[str, str]:
    """校验并保存用户上传的图片。

    Args:
        file: FastAPI 上传的 UploadFile（multipart 的 file 字段）。
        prefix: 文件名前缀（如 "team_12"、"verify"），用于区分不同类型的图片。
        max_mb: 允许的最大体积（MB）。

    Returns:
        (url, abs_path)：可访问的网络路径（/uploads/xxx）与本地绝对路径。
        网络路径用于存库并回显；绝对路径供 AI 视觉模型读取。

    Raises:
        HTTPException(400): 类型不支持或超过大小限制。
    """
    # 仅允许图片类型
    if file.content_type not in _ALLOWED:
        raise HTTPException(status_code=400, detail="仅支持 JPG/PNG/WebP 格式图片")

    content = file.file.read()
    if len(content) > max_mb * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f"图片大小不能超过 {max_mb}MB")

    ext = _ALLOWED[file.content_type]
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    # 带时间戳（毫秒）防文件重名冲突
    filename = f"{prefix}_{int(time.time() * 1000)}{ext}"
    file_path = upload_dir / filename
    file_path.write_bytes(content)

    return f"/uploads/{filename}", str(file_path)
