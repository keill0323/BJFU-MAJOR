"""
通用上传服务
Author: keill
Since: 2026-08

统一处理「用户上传图片」的校验与落盘，避免在每个上传接口里重复写
「类型校验 + 大小校验 + 拼文件名 + 写文件」这套逻辑。

安全审查修复（#9/#10）：
- 增加文件头魔数校验，防止伪造 content-type 上传任意文件（存储型 XSS 载体）；
- 文件名改为「前缀 + 毫秒时间戳 + 随机串」，不可枚举（学信网截图含姓名学号）。
"""
import secrets
import time
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, UploadFile

from app.config import settings

# 允许的图片类型 → 落盘扩展名
_ALLOWED = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


def _sniff_image_ext(content: bytes) -> Optional[str]:
    """按文件头魔数识别真实图片格式，返回扩展名（.jpg/.png/.webp），无法识别返回 None

    不信任客户端声明的 content-type：仅凭 content-type 校验时，
    攻击者可声明 image/png 上传 HTML/SVG 等任意内容。
    """
    if len(content) >= 3 and content[:3] == b"\xff\xd8\xff":
        return ".jpg"          # JPEG
    if len(content) >= 8 and content[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"          # PNG
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return ".webp"         # WebP
    return None


def save_upload_image(file: UploadFile, prefix: str, max_mb: int = 10) -> tuple[str, str]:
    """校验并保存用户上传的图片。

    Args:
        file: FastAPI 上传的 UploadFile（multipart 的 file 字段）。
        prefix: 文件名前缀（如 "team_12"、"verify"、"avatar"、"rank"），用于区分不同类型的图片。
        max_mb: 允许的最大体积（MB）。

    Returns:
        (url, abs_path)：可访问的网络路径（/uploads/xxx）与本地绝对路径。
        网络路径用于存库并回显；绝对路径供 AI 视觉模型读取。

    Raises:
        HTTPException(400): 类型不支持（声明类型非法 或 魔数与真实格式不符）或超过大小限制。
    """
    # ① 声明类型白名单
    if file.content_type not in _ALLOWED:
        raise HTTPException(status_code=400, detail="仅支持 JPG/PNG/WebP 格式图片")

    # ② 读取内容并限制大小
    content = file.file.read()
    if len(content) > max_mb * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f"图片大小不能超过 {max_mb}MB")

    # ③ 魔数校验：文件真实格式必须与声明类型一致（防伪造 content-type）
    real_ext = _sniff_image_ext(content)
    declared_ext = _ALLOWED[file.content_type]
    if real_ext is None:
        raise HTTPException(status_code=400, detail="文件内容不是有效的图片")
    if real_ext != declared_ext:
        raise HTTPException(status_code=400, detail="文件实际格式与声明的类型不一致，已拒绝")

    # ④ 落盘：文件名带随机串，防止外部枚举上传文件（截图含隐私信息）
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{prefix}_{int(time.time() * 1000)}_{secrets.token_hex(8)}{real_ext}"
    file_path = upload_dir / filename
    file_path.write_bytes(content)

    return f"/uploads/{filename}", str(file_path)
