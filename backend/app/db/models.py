from sqlalchemy import Column, String, DateTime, Text
from datetime import datetime, timezone
from app.db.database import Base


class Shipment(Base):
    __tablename__ = "shipments"

    tracking_id = Column(String, primary_key=True)
    status = Column(String)
    location = Column(String)
    eta = Column(String)
    customer_id = Column(String, index=True)
    origin = Column(String)
    destination = Column(String)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(String, primary_key=True)
    customer_id = Column(String, index=True)
    tracking_id = Column(String)
    message = Column(Text)
    notification_type = Column(String)  # delay, eta_update, delivered, exception
    is_read = Column(String, default="false")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))