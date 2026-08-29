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

class CustomerPreference(Base):
    """Durable customer memory written by the agent (e.g. delivery_window:
    'after 6pm'). Injected into the planner's system prompt every turn so
    the assistant personalizes without being reminded."""
    __tablename__ = "customer_preferences"

    id = Column(String, primary_key=True)
    customer_id = Column(String, index=True)
    preference = Column(String)  # e.g. delivery_window, preferred_pickup, contact_channel
    value = Column(Text)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class SupportCase(Base):
    """Human-handoff record. The agent writes the summary itself from the
    conversation, so a support rep starts with context instead of a cold
    transcript."""
    __tablename__ = "support_cases"

    id = Column(String, primary_key=True)  # e.g. CASE-1A2B3C4D
    customer_id = Column(String, index=True)
    tracking_id = Column(String)
    summary = Column(Text)
    priority = Column(String, default="normal")  # low, normal, high
    status = Column(String, default="open")      # open, in_progress, resolved
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
