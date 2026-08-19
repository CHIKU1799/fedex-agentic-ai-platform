"""
Shipment operations — the single source of truth for business rules and
authorization. Both the REST routes and the agentic tool layer call these,
so a rule (e.g. "cannot cancel a delivered shipment") or an authorization
check can never be enforced in one entry point but forgotten in the other.

Each function raises fastapi.HTTPException on a rule/authorization failure.
The agentic tool layer catches those and relays them to the model as a
structured error; the REST layer lets FastAPI turn them into HTTP responses.
"""
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.security import User, authorize_shipment_access
from app.db.crud import (
    get_shipment,
    list_shipments_by_customer,
    update_shipment,
    cancel_shipment as crud_cancel,
    create_notification,
    get_notifications,
)
from app.services import weather_service


def _shipment_or_404(db: Session, tracking_id: str):
    shipment = get_shipment(db, tracking_id)
    if not shipment:
        raise HTTPException(status_code=404, detail=f"Shipment {tracking_id} not found")
    return shipment


def track(db: Session, user: User, tracking_id: str) -> dict:
    # Tracking by ID is public by design (matches real carrier behaviour:
    # possession of the tracking number is the authorization). Mutations and
    # notification reads below are strictly access-controlled.
    shipment = _shipment_or_404(db, tracking_id)
    return {
        "tracking_id": shipment.tracking_id,
        "status": shipment.status,
        "location": shipment.location,
        "eta": shipment.eta,
        "origin": shipment.origin,
        "destination": shipment.destination,
    }


def reschedule(db: Session, user: User, tracking_id: str, new_date: str) -> dict:
    shipment = _shipment_or_404(db, tracking_id)
    authorize_shipment_access(user, shipment.customer_id)
    if shipment.status == "cancelled":
        raise HTTPException(status_code=400, detail="Cannot reschedule a cancelled shipment")
    updated = update_shipment(db, tracking_id, {"eta": new_date, "status": "rescheduled"})
    if shipment.customer_id:
        create_notification(
            db, shipment.customer_id, tracking_id,
            f"Your delivery has been rescheduled to {new_date}.", "eta_update",
        )
    return {"tracking_id": tracking_id, "new_eta": updated.eta, "status": updated.status}


def redirect(db: Session, user: User, tracking_id: str, new_address: str) -> dict:
    shipment = _shipment_or_404(db, tracking_id)
    authorize_shipment_access(user, shipment.customer_id)
    if shipment.status in ("delivered", "cancelled"):
        raise HTTPException(status_code=400, detail=f"Cannot redirect a {shipment.status} shipment")
    updated = update_shipment(db, tracking_id, {"destination": new_address, "status": "redirected"})
    if shipment.customer_id:
        create_notification(
            db, shipment.customer_id, tracking_id,
            f"Your package has been redirected to {new_address}.", "eta_update",
        )
    return {"tracking_id": tracking_id, "new_destination": updated.destination, "status": updated.status}


def cancel(db: Session, user: User, tracking_id: str, reason: str = "") -> dict:
    shipment = _shipment_or_404(db, tracking_id)
    authorize_shipment_access(user, shipment.customer_id)
    if shipment.status == "delivered":
        raise HTTPException(status_code=400, detail="Cannot cancel a delivered shipment")
    cancelled = crud_cancel(db, tracking_id)
    if shipment.customer_id:
        create_notification(
            db, shipment.customer_id, tracking_id,
            f"Your shipment has been cancelled. Reason: {reason or 'N/A'}", "exception",
        )
    return {"tracking_id": cancelled.tracking_id, "status": cancelled.status}


def list_for_customer(db: Session, user: User, customer_id: str) -> dict:
    # A customer may only list their own shipments.
    authorize_shipment_access(user, customer_id)
    rows = list_shipments_by_customer(db, customer_id)
    return {
        "customer_id": customer_id,
        "shipments": [
            {
                "tracking_id": s.tracking_id,
                "status": s.status,
                "location": s.location,
                "eta": s.eta,
                "destination": s.destination,
            }
            for s in rows
        ],
    }


def weather_impact(db: Session, user: User, tracking_id: str) -> dict:
    """
    Assess weather-related delivery risk at a shipment's destination.
    Read access mirrors track(): possession of the tracking number is the
    authorization. When risk is high for an active shipment, a proactive
    notification is created for the owning customer (deduplicated so
    repeated checks do not spam the inbox).
    """
    shipment = _shipment_or_404(db, tracking_id)
    report = weather_service.destination_weather(shipment.destination, shipment.eta)
    result = {
        "tracking_id": shipment.tracking_id,
        "status": shipment.status,
        "destination": shipment.destination,
        "eta": shipment.eta,
        "weather": report,
        "notification_created": False,
    }
    active = shipment.status not in ("delivered", "cancelled")
    if report.get("available") and report.get("risk") == "high" and active and shipment.customer_id:
        already_alerted = any(
            n.tracking_id == tracking_id
            and n.notification_type == "weather_alert"
            and n.is_read != "true"
            for n in get_notifications(db, shipment.customer_id)
        )
        if not already_alerted:
            create_notification(
                db, shipment.customer_id, tracking_id,
                f"Weather alert: {report['conditions']} expected near "
                f"{report.get('location', shipment.destination)} around {report.get('forecast_date', shipment.eta)}. "
                f"Your delivery (ETA {shipment.eta}) may be delayed. You can reschedule or redirect if needed.",
                "weather_alert",
            )
            result["notification_created"] = True
    return result


def suggest_delivery_dates(db: Session, user: User, tracking_id: str) -> dict:
    """
    Weather-aware reschedule suggestions: classify the next week at the
    destination and recommend the earliest low-risk date. Read-only, same
    access model as track(). The agent chains this with reschedule() to
    plan around bad weather.
    """
    shipment = _shipment_or_404(db, tracking_id)
    if shipment.status in ("delivered", "cancelled"):
        raise HTTPException(status_code=400, detail=f"Cannot suggest dates for a {shipment.status} shipment")
    outlook = weather_service.upcoming_daily_risk(shipment.destination, days=7)
    result = {
        "tracking_id": shipment.tracking_id,
        "destination": shipment.destination,
        "current_eta": shipment.eta,
        "outlook": outlook,
        "recommended_date": None,
    }
    if outlook.get("available"):
        # Skip today (index 0): same-day reschedules are not offered.
        for day in outlook["days"][1:]:
            if day["risk"] == "low":
                result["recommended_date"] = day["date"]
                break
    return result


def hold_at_location(db: Session, user: User, tracking_id: str, location: str = "") -> dict:
    """
    Ask for the package to be held for pickup at a FedEx facility instead
    of delivered. Mutation: requires ownership, blocked for finished
    shipments, and notifies the customer.
    """
    shipment = _shipment_or_404(db, tracking_id)
    authorize_shipment_access(user, shipment.customer_id)
    if shipment.status in ("delivered", "cancelled"):
        raise HTTPException(status_code=400, detail=f"Cannot hold a {shipment.status} shipment")
    pickup_point = location or f"FedEx facility nearest to {shipment.destination}"
    updated = update_shipment(db, tracking_id, {"status": "hold_requested"})
    if shipment.customer_id:
        create_notification(
            db, shipment.customer_id, tracking_id,
            f"Your package will be held for pickup at {pickup_point}. Bring a photo ID to collect it.",
            "eta_update",
        )
    return {"tracking_id": tracking_id, "status": updated.status, "pickup_location": pickup_point}


def notifications_for(db: Session, user: User, customer_id: str) -> dict:
    # A customer may only read their own notifications.
    authorize_shipment_access(user, customer_id)
    rows = get_notifications(db, customer_id)
    return {
        "customer_id": customer_id,
        "notifications": [
            {
                "id": n.id,
                "tracking_id": n.tracking_id,
                "message": n.message,
                "notification_type": n.notification_type,
                "is_read": n.is_read,
            }
            for n in rows
        ],
    }
