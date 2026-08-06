import uuid
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.db.models import Shipment, Notification


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