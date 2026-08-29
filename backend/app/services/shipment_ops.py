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
    notify_once,
    upsert_preference,
    get_preferences,
    create_support_case,
    list_support_cases,
)
from app.services import facility_service, route_service, weather_service


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
    # Only alert when the forecast actually covers the ETA — a "high risk"
    # reading for some other (closest available) day is context, not an alert.
    if (report.get("available") and report.get("risk") == "high" and active
            and report.get("eta_within_forecast", True)):
        created = notify_once(
            db, shipment.customer_id, tracking_id,
            f"Weather alert: {report['conditions']} expected near "
            f"{report.get('location', shipment.destination)} around {report.get('forecast_date', shipment.eta)}. "
            f"Your delivery (ETA {shipment.eta}) may be delayed. You can reschedule or redirect if needed.",
            "weather_alert",
        )
        result["notification_created"] = created is not None
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
    facility = None
    if location:
        pickup_point = location
    else:
        facility = facility_service.nearest_facility(shipment.destination)
        pickup_point = (
            f"{facility['name']}, {facility['address']}" if facility
            else f"the FedEx facility nearest to {shipment.destination} (exact address will be confirmed)"
        )
    updated = update_shipment(db, tracking_id, {"status": "hold_requested"})
    if shipment.customer_id:
        create_notification(
            db, shipment.customer_id, tracking_id,
            f"Your package will be held for pickup at {pickup_point}. Bring a photo ID to collect it.",
            "eta_update",
        )
    result = {"tracking_id": tracking_id, "status": updated.status, "pickup_location": pickup_point}
    if facility:
        result["facility"] = facility
    return result


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


def save_preference(db: Session, user: User, customer_id: str, preference: str, value: str) -> dict:
    """
    Durable agent memory: remember a customer preference (delivery window,
    preferred pickup point, contact channel...). Write access mirrors the
    mutation rules — a customer can only write their own memory.
    """
    authorize_shipment_access(user, customer_id)
    preference = (preference or "").strip().lower().replace(" ", "_")[:64]
    value = (value or "").strip()[:500]
    if not preference or not value:
        raise HTTPException(status_code=400, detail="Both preference and value are required")
    row = upsert_preference(db, customer_id, preference, value)
    return {"customer_id": customer_id, "preference": row.preference, "value": row.value, "saved": True}


def preferences_for(db: Session, user: User, customer_id: str) -> dict:
    authorize_shipment_access(user, customer_id)
    rows = get_preferences(db, customer_id)
    return {
        "customer_id": customer_id,
        "preferences": [{"preference": r.preference, "value": r.value} for r in rows],
    }


def open_support_case(db: Session, user: User, customer_id: str, summary: str,
                      tracking_id: str = "", priority: str = "normal") -> dict:
    """
    Human handoff: the agent escalates with a self-written case summary so
    a support rep starts with context. Creating a case notifies the customer
    with the reference number.
    """
    authorize_shipment_access(user, customer_id)
    summary = (summary or "").strip()[:2000]
    if not summary:
        raise HTTPException(status_code=400, detail="A case summary is required")
    if priority not in ("low", "normal", "high"):
        priority = "normal"
    if tracking_id:
        # A case may reference a shipment; verify it exists so refs stay clean.
        _shipment_or_404(db, tracking_id)
    case = create_support_case(db, customer_id, summary, tracking_id, priority)
    create_notification(
        db, customer_id, tracking_id or "",
        f"Support case {case.id} has been opened for you (priority: {case.priority}). "
        "A FedEx representative will follow up shortly.",
        "support_case",
    )
    return {
        "case_id": case.id,
        "customer_id": customer_id,
        "tracking_id": tracking_id,
        "priority": case.priority,
        "status": case.status,
        "summary": case.summary,
    }


def support_cases_for(db: Session, user: User, customer_id: str = "") -> dict:
    """Customers see their own cases; agents may list everyone's (empty id)."""
    if customer_id:
        authorize_shipment_access(user, customer_id)
    elif user.role != "agent":
        raise HTTPException(status_code=403, detail="Only FedEx agents can list all cases.")
    rows = list_support_cases(db, customer_id)
    return {
        "customer_id": customer_id or "all",
        "cases": [
            {
                "case_id": c.id,
                "customer_id": c.customer_id,
                "tracking_id": c.tracking_id,
                "summary": c.summary,
                "priority": c.priority,
                "status": c.status,
            }
            for c in rows
        ],
    }


# Rough journey completion per status, for the animated map. When the
# package's current location matches a hub on the planned route, the real
# position wins over this heuristic.
_PROGRESS_BY_STATUS = {
    "created": 0.05,
    "picked_up": 0.15,
    "in_transit": 0.5,
    "delayed": 0.45,
    "exception": 0.45,
    "rescheduled": 0.5,
    "redirected": 0.55,
    "out_for_delivery": 0.88,
    "delivery_attempt_failed": 0.9,
    "hold_requested": 0.9,
    "delivered": 1.0,
    "cancelled": 0.0,
}


def journey_for(db: Session, user: User, tracking_id: str) -> dict:
    """
    Everything the animated journey map needs in one payload: the Dijkstra
    hub route (weather-adjusted), per-hub weather risk, progress along the
    route, and a live time-to-delivery countdown. Read access mirrors
    track(): possession of the tracking number is the authorization.
    """
    from datetime import datetime, timezone

    shipment = _shipment_or_404(db, tracking_id)

    risk_cache = {}

    def _hub_risk(city: str) -> str:
        if city not in risk_cache:
            report = weather_service.destination_weather(city, shipment.eta)
            risk_cache[city] = report.get("risk", "low") if report.get("available") else "low"
        return risk_cache[city]

    route = route_service.plan_route(shipment.origin, shipment.destination, risk_lookup=_hub_risk)

    # Time-to-delivery countdown (assume end-of-day delivery on the ETA date).
    hours_remaining = None
    try:
        eta_moment = datetime.strptime(shipment.eta.strip()[:10], "%Y-%m-%d").replace(
            hour=18, tzinfo=timezone.utc)
        hours_remaining = max(0, int((eta_moment - datetime.now(timezone.utc)).total_seconds() // 3600))
    except (ValueError, AttributeError):
        pass
    if shipment.status == "delivered":
        hours_remaining = 0

    progress = _PROGRESS_BY_STATUS.get(shipment.status, 0.5)
    if route.get("available"):
        hubs = [h["name"] for h in route["route"]["hubs"]]
        location = (shipment.location or "").lower()
        for i, hub in enumerate(hubs):
            if hub.lower() in location and len(hubs) > 1:
                progress = max(0.05, min(0.95, i / (len(hubs) - 1)))
                break

    return {
        "tracking_id": shipment.tracking_id,
        "status": shipment.status,
        "origin": shipment.origin,
        "destination": shipment.destination,
        "current_location": shipment.location,
        "eta": shipment.eta,
        "hours_remaining": hours_remaining,
        "days_remaining": None if hours_remaining is None else round(hours_remaining / 24, 1),
        "progress": round(progress, 3),
        "route": route,
    }
