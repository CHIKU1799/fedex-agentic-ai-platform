import uuid
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from app.db.models import Shipment, Notification, CustomerPreference, SupportCase


# ── Shipment CRUD ──

def get_shipment(db: Session, tracking_id: str):
    return db.query(Shipment).filter(Shipment.tracking_id == tracking_id).first()


def list_shipments_by_customer(db: Session, customer_id: str):
    return (
        db.query(Shipment)
        .filter(Shipment.customer_id == customer_id)
        .order_by(Shipment.created_at.desc())
        .all()
    )


def create_shipment(db: Session, shipment_data: dict):
    shipment = Shipment(**shipment_data)
    db.add(shipment)
    db.commit()
    db.refresh(shipment)
    return shipment


def update_shipment(db: Session, tracking_id: str, updates: dict):
    shipment = get_shipment(db, tracking_id)
    if not shipment:
        return None
    for key, value in updates.items():
        setattr(shipment, key, value)
    shipment.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(shipment)
    return shipment


def cancel_shipment(db: Session, tracking_id: str):
    shipment = get_shipment(db, tracking_id)
    if not shipment:
        return None
    shipment.status = "cancelled"
    shipment.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(shipment)
    return shipment


# ── Notification CRUD ──

def create_notification(db: Session, customer_id: str, tracking_id: str, message: str, notification_type: str):
    notification = Notification(
        id=str(uuid.uuid4()),
        customer_id=customer_id,
        tracking_id=tracking_id,
        message=message,
        notification_type=notification_type,
    )
    db.add(notification)
    db.commit()
    db.refresh(notification)
    return notification


def get_notifications(db: Session, customer_id: str):
    return db.query(Notification).filter(Notification.customer_id == customer_id).order_by(Notification.created_at.desc()).all()


def mark_notification_read(db: Session, notification_id: str):
    notification = db.query(Notification).filter(Notification.id == notification_id).first()
    if not notification:
        return None
    notification.is_read = "true"
    db.commit()
    db.refresh(notification)
    return notification

# ── Customer preference CRUD (agent memory) ──

def upsert_preference(db: Session, customer_id: str, preference: str, value: str):
    row = (
        db.query(CustomerPreference)
        .filter(CustomerPreference.customer_id == customer_id,
                CustomerPreference.preference == preference)
        .first()
    )
    if row:
        row.value = value
        row.updated_at = datetime.now(timezone.utc)
    else:
        row = CustomerPreference(
            id=str(uuid.uuid4()), customer_id=customer_id,
            preference=preference, value=value,
        )
        db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_preferences(db: Session, customer_id: str):
    return (
        db.query(CustomerPreference)
        .filter(CustomerPreference.customer_id == customer_id)
        .order_by(CustomerPreference.preference)
        .all()
    )


# ── Support case CRUD (human handoff) ──

def create_support_case(db: Session, customer_id: str, summary: str,
                        tracking_id: str = "", priority: str = "normal"):
    case = SupportCase(
        id=f"CASE-{uuid.uuid4().hex[:8].upper()}",
        customer_id=customer_id,
        tracking_id=tracking_id,
        summary=summary,
        priority=priority,
    )
    db.add(case)
    db.commit()
    db.refresh(case)
    return case


def list_support_cases(db: Session, customer_id: str = ""):
    query = db.query(SupportCase)
    if customer_id:
        query = query.filter(SupportCase.customer_id == customer_id)
    return query.order_by(SupportCase.created_at.desc()).all()


def notify_once(db: Session, customer_id: str, tracking_id: str, message: str,
                notification_type: str, window_hours: int = 24):
    """Create a notification unless the same shipment/type was already
    notified and is either still unread OR was created inside the dedupe
    window. Stops both inbox spam and the read-then-refire duplicate."""
    if not customer_id:
        return None
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    existing = (
        db.query(Notification)
        .filter(
            Notification.customer_id == customer_id,
            Notification.tracking_id == tracking_id,
            Notification.notification_type == notification_type,
        )
        .all()
    )
    for n in existing:
        created = n.created_at
        if created is not None and created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if n.is_read != "true" or (created is not None and created >= cutoff):
            return None
    return create_notification(db, customer_id, tracking_id, message, notification_type)
