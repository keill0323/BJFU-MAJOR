"""
AI 截图审核服务（智谱 GLM-5V-Turbo 视觉模型）
Author: keill
Since: 2026-08-11
"""
import base64
import json
import urllib.request

from app.config import settings

# 审核指令
REVIEW_PROMPT = """请审核这张学信网/校园卡截图，用于「北京林业大学」校内赛事报名认证。
请检查：
1. 学校名称是否为「北京林业大学」（截图可能写作：北京林业大学、北林、Beijing Forestry University、BJFU，均视为同一学校）
2. 是否包含姓名和学号
3. 是否有明显P图、涂抹、拼接痕迹
4. 是否像本人上传的截图
特别注意：如果截图中的学校不是北京林业大学（是其他学校），必须判定 is_valid=false。
请只返回 JSON 格式（不要任何其他文字），格式如下：
{"is_valid": true或false, "confidence": 0到1之间的小数, "reason": "判断理由（中文简短）", "student_id": "截图中的学号，没有则写null"}
如果不确定，confidence 给低值（如 0.5 以下）。"""


def review_image(image_path):
    """审核一张图片，返回结构化结果 dict"""
    # ① 图片转 base64（根据扩展名判断 mime 类型）
    with open(image_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode()
    ext = image_path.rsplit(".", 1)[-1].lower()
    mime = "image/png" if ext == "png" else "image/jpeg"

    # ② 构造请求（OpenAI 兼容格式）
    payload = {
        "model": settings.AI_MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
                {"type": "text", "text": REVIEW_PROMPT},
            ],
        }],
    }
    req = urllib.request.Request(
        settings.AI_BASE_URL + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": "Bearer " + settings.AI_API_KEY,
            "Content-Type": "application/json",
        },
    )

    # ③ 发请求拿回复
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode())
    content = data["choices"][0]["message"]["content"]

    # ④ 解析模型返回的 JSON
    return _parse_json(content)


def _parse_json(text):
    """从模型返回的文字里提取 JSON（容错处理）"""
    try:
        return json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
        return {"is_valid": None, "confidence": 0, "reason": text, "student_id": None}