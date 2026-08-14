"""临时测试：用正式服务审核一张图"""
from app.services.ai_review_service import review_image

result = review_image("uploads/verify_21_1786286575.jpg")
print("审核结果:", result)
