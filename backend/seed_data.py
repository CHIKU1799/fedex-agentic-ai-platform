"""
Run this once to populate the database with sample shipments and notifications.
Usage: python -m seed_data   (from the backend/ folder)
"""
from app.db.database import Base, engine, SessionLocal
from app.db.models import Shipment, Notification
import uuid
from datetime import date, datetime, timedelta, timezone

Base.metadata.create_all(bind=engine)

db = SessionLocal()


def _eta(days_from_now: int) -> str:
    """ETAs are seeded relative to today so they always fall inside the
    16-day weather forecast window (real forecasts, believable demo)."""
    return (date.today() + timedelta(days=days_from_now)).isoformat()


# ── Sample Shipments ──
shipments = [
    {
        "tracking_id": "FX100001",
        "status": "in_transit",
        "location": "Memphis, TN",
        "eta": _eta(3),
        "customer_id": "CUST001",
        "origin": "Los Angeles, CA",
        "destination": "New York, NY",
    },
    {
        "tracking_id": "FX100002",
        "status": "out_for_delivery",
        "location": "Chicago, IL",
        "eta": _eta(0),
        "customer_id": "CUST001",
        "origin": "Seattle, WA",
        "destination": "Chicago, IL",
    },
    {
        "tracking_id": "FX100003",
        "status": "delivered",
        "location": "Dallas, TX",
        "eta": _eta(-2),
        "customer_id": "CUST002",
        "origin": "Miami, FL",
        "destination": "Dallas, TX",
    },
    {
        "tracking_id": "FX100004",
        "status": "delayed",
        "location": "Denver, CO",
        "eta": _eta(5),
        "customer_id": "CUST002",
        "origin": "Boston, MA",
        "destination": "San Francisco, CA",
    },
    {
        "tracking_id": "FX100005",
        "status": "created",
        "location": "Atlanta, GA",
        "eta": _eta(8),
        "customer_id": "CUST003",
        "origin": "Atlanta, GA",
        "destination": "Portland, OR",
    },
    {
        # The most common real-world failure mode: nobody home.
        "tracking_id": "FX100006",
        "status": "delivery_attempt_failed",
        "location": "Brooklyn, NY",
        "eta": _eta(1),
        "customer_id": "CUST001",
        "origin": "Chicago, IL",
        "destination": "New York, NY",
    },
]

# ── Sample Notifications ──
notifications = [
    {
        "id": str(uuid.uuid4()),
        "customer_id": "CUST001",
        "tracking_id": "FX100001",
        "message": "Your package is in transit and currently in Memphis, TN.",
        "notification_type": "eta_update",
        "is_read": "false",
    },
    {
        "id": str(uuid.uuid4()),
        "customer_id": "CUST001",
        "tracking_id": "FX100002",
        "message": "Your package is out for delivery today!",
        "notification_type": "eta_update",
        "is_read": "false",
    },
    {
        "id": str(uuid.uuid4()),
        "customer_id": "CUST002",
        "tracking_id": "FX100004",
        "message": f"Your shipment has been delayed due to weather conditions. New ETA: {_eta(5)}.",
        "notification_type": "delay",
        "is_read": "false",
    },
    {
        "id": str(uuid.uuid4()),
        "customer_id": "CUST001",
        "tracking_id": "FX100006",
        "message": "We missed you: a delivery attempt was unsuccessful. Reschedule or hold for pickup anytime.",
        "notification_type": "delivery_attempt",
        "is_read": "false",
    },
]

# ── Insert data ──
for s in shipments:
    existing = db.query(Shipment).filter(Shipment.tracking_id == s["tracking_id"]).first()
    if not existing:
        db.add(Shipment(**s))
        print(f"  Added shipment {s['tracking_id']}")
    else:
        print(f"  Shipment {s['tracking_id']} already exists, skipping")

# Idempotent: only seed notifications if none exist yet, so re-running the
# script doesn't create duplicate notifications on every invocation.
if db.query(Notification).count() == 0:
    for n in notifications:
        db.add(Notification(**n))
        print(f"  Added notification for {n['customer_id']}")
else:
    print("  Notifications already present, skipping")

db.commit()
db.close()

print("\nSeed data loaded successfully!")
print("Sample tracking IDs: FX100001, FX100002, FX100003, FX100004, FX100005, FX100006")
print("Sample customer IDs: CUST001, CUST002, CUST003")
