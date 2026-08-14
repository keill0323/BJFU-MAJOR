"""临时测试AI功能"""
import base64
import json
import urllib.request

from app.config import settings

#测试用图
IMAGE_PATH = "uploads/verify_21_1786286575.jpg"

#读取图片并进行base64编码
with open(IMAGE_PATH, "rb") as f:
    image_b64 = base64.b64encode(f.read()).decode()

#构造请求体(OpenAI格式)
payload = {
    "model": settings.AI_MODEL,
    "messages": [
        {
            "role": "user",
            "content": [
                #图片部分:base64前加data:image/jpeg;base64, 前缀
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                #文本部分:描述图片
                {"type": "text", "text": "请描述这张图片的内容，用中文回答。"},
            ],
        }
    ],
}

#发送请求到智谱API
req = urllib.request.Request(
    settings.AI_BASE_URL + "/chat/completions",
    data=json.dumps(payload).encode(),
    headers={
        "Authorization": "Bearer " + settings.AI_API_KEY,
        "Content-Type": "application/json",
    },
)


#发送请求并获取响应
try:
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode())
    result = data["choices"][0]["message"]["content"]
    print("AI审核结果:", result)
except Exception as e:
    print("调用失败: ", e)
