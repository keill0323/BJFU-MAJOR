"""Explicit, idempotent creation of the schedule inbox. No historical events invented."""
from app.models.notification import ScheduleNotification


def upgrade(engine):
    ScheduleNotification.__table__.create(engine, checkfirst=True)


if __name__ == "__main__":
    from app.database import engine
    upgrade(engine)
    print("schedule_notifications ready")
