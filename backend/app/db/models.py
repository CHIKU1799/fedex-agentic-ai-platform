from sqlalchemy import Column, Float, String, DateTime, Text
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


class AgentEvent(Base):
    """Audit trail of every action the agent takes (agentic loop, fallback
    router, fleet watch, verification checks). Powers the agentic-analytics
    dashboard and the customer-facing activity feed: what the AI actually
    did, why, how reliably, and what it saved."""
    __tablename__ = "agent_events"

    id = Column(String, primary_key=True)
    username = Column(String)
    role = Column(String)
    tool = Column(String, index=True)
    ok = Column(String, default="true")        # "true" / "false"
    provider = Column(String, default="")      # anthropic / groq / offline / fleet_watch
    tracking_id = Column(String, default="")
    customer_id = Column(String, index=True, default="")
    note = Column(Text, default="")            # the "reason" column of the action log
    cost_avoided = Column(Float, default=0.0)  # $ vs the failed-attempt benchmark
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class DeliveryCommitment(Base):
    """An outcome the agent is responsible for ("deliver FX123 by Friday").
    The monitoring loop re-evaluates every active commitment against ETA,
    status and weather, marks it AT_RISK/MET/MISSED, and intervenes."""
    __tablename__ = "delivery_commitments"

    id = Column(String, primary_key=True)      # e.g. C-1A2B3C4D
    tracking_id = Column(String, index=True)
    customer_id = Column(String, index=True)
    goal_type = Column(String, default="DELIVER_BY")
    target_date = Column(String)               # YYYY-MM-DD
    status = Column(String, default="ACTIVE")  # ACTIVE, AT_RISK, MET, MISSED, CANCELLED
    priority = Column(String, default="normal")
    risk_reason = Column(Text, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
