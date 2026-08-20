"""
AI 截图审核服务（智谱 GLM-5V-Turbo 视觉模型）
Author: keill
Since: 2026-08-11
"""
import base64
import json
import re
import urllib.request

from app.config import settings

# 审核指令
REVIEW_PROMPT = """请审核这张学信网截图 / 教务系统截图 / 校园卡（学生证）截图 / 电子校园卡截图，用于「北京林业大学」校内赛事报名认证。
四种凭证均有效：学信网截图、教务系统截图、校园卡（学生证）、电子校园卡。
截图应为点进学校后显示的学生资料详情卡片（包含学校名、姓名、学号）。
请检查：
1. 学校名称是否为「北京林业大学」（截图可能写作：北京林业大学、北林、Beijing Forestry University、BJFU，均视为同一学校）
2. 是否包含姓名和学号（校园卡/电子校园卡若只有卡号而无学号，不视为有效）
3. 是否有明显P图、涂抹、拼接痕迹
4. 是否像本人上传的截图
特别注意：如果截图中的学校不是北京林业大学（是其他学校），必须判定 is_valid=false。
请只返回 JSON 格式（不要任何其他文字），格式如下：
{"is_valid": true或false, "confidence": 0到1之间的小数, "reason": "判断理由（中文简短）", "student_id": "截图中的学号，没有则写null"}
如果不确定，confidence 给低值（如 0.5 以下）。"""


# 段位识别指令
REVIEW_RANK_PROMPT = """请识别这张游戏平台段位截图中的玩家段位（支持完美平台和5E平台）。

平台识别与取值规则：
1. 完美平台：界面有「天梯历史」和「天梯当前」两处段位，请取「天梯历史」（历史最高）那个段位
2. 5E平台：界面有「当前赛季数据」卡片，请直接识别卡片里的当前段位（5E主页没有历史最高标注）
3. 两个平台段位体系相同，取值范围：D、C、C+、C++、B、B+、B++、A、A+、A++、S
   - 金色/亮色图标通常对应 ++ 段位（如 C++、B++、A++）
   - D 段只有 D（没有 D+、D++）
   - S 段带星数（0~50 星），返回「S+星数」格式（如 S0、S10、S25、S50）；若界面只显示 S 看不清星数，返回 S
请只返回 JSON 格式（不要任何其他文字）：
{"rank": "识别到的段位（如 C++ 或 S25，无法识别写 null）", "confidence": 0到1之间的小数, "reason": "判断理由（中文简短）"}
如果不确定，confidence 给低值（如 0.5 以下）。"""

# 合法段位（AI 返回结果需归一化到这些值）
VALID_RANKS = {"D", "C", "C+", "C++", "B", "B+", "B++", "A", "A+", "A++"}


def _call_vision(image_path, prompt):
    """通用：图片转 base64 + 调视觉模型，返回解析后的 JSON dict"""
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
                {"type": "text", "text": prompt},
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


def review_image(image_path):
    """审核学信网/校园卡截图"""
    return _call_vision(image_path, REVIEW_PROMPT)


def normalize_rank(rank):
    """把 AI 返回的段位归一化到合法值（D/C/C+/C++/B/B+/B++/A/A+/A++/S1~S50），非法返回 None"""
    if not rank:
        return None
    rank = str(rank).strip().upper().replace(" ", "")
    m = re.match(r"^S(\d*)$", rank)
    if m:
        n_str = m.group(1)
        # S（无星数）或 S0 星 → 都归到最低 S1
        n = int(n_str) if n_str else 0
        if 0 <= n <= 50:
            return "S1" if n == 0 else f"S{n}"
        return None
    if rank in VALID_RANKS:
        return rank
    return None


def review_rank(image_path):
    """识别游戏段位截图，返回 {rank, confidence, reason}"""
    result = _call_vision(image_path, REVIEW_RANK_PROMPT)
    result["rank"] = normalize_rank(result.get("rank"))
    return result


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