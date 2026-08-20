"""
临时测试脚本：调用 AI 识别游戏段位截图
用法（在 backend 目录下，已激活 .venv）：
    python test_rank.py <图片路径1> [图片路径2] ...
"""
import sys

from app.services import ai_review_service

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python test_rank.py <图片路径1> [图片路径2] ...")
        sys.exit(1)

    for path in sys.argv[1:]:
        print(f"\n===== 识别: {path} =====")
        try:
            result = ai_review_service.review_rank(path)
            print("AI 返回:", result)
        except Exception as e:
            print("识别失败:", e)
