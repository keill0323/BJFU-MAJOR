"""仅添加新表；可重复执行，不修改现有赛事/用户。"""
from app.database import engine
from app.models.recruitment import RecruitmentPost, WechatSubscription, WechatOutbox


def upgrade(bind=engine):
    for model in (RecruitmentPost, WechatSubscription, WechatOutbox):
        model.__table__.create(bind, checkfirst=True)


if __name__ == "__main__":
    upgrade()
    print("Recruitment and subscription tables ready")
