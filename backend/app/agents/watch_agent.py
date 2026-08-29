"""
Fleet-watch agent: autonomy without a prompt.

The chat planner is agentic *reactively* — it plans tool calls when a
customer asks. This agent is agentic *proactively*: it sweeps every active
shipment on its own (scheduled cron or an ops-console trigger), detects
delays, exceptions, and weather threats at each destination, decides on a
recommended action, and notifies the affected customer before they ever
ask "where is my package?".

Notifications are deduplicated per shipment/type against unread inbox
items, so repeated sweeps never spam a customer.
"""
from sqlalchemy.orm import Session

from app.core.logger import get_logger
from app.db.crud import notify_once
from app.db.models import Shipment
from app.services import weather_service

logger = get_logger()

_ACTIVE_EXCLUDED = ("delivered", "cancelled")
_TROUBLE_STATUSES = ("delayed", "exception", "delivery_attempt_failed")


def _notify_once(db: Session, customer_id: str, tracking_id: str,
                 notification_type: str, message: str) -> bool:
    """Time-windowed dedupe shared with the reactive path (crud.notify_once):
    suppresses while unread AND for 24h after creation even if read."""
    return notify_once(db, customer_id, tracking_id, message, notification_type) is not None


def _earliest_low_risk_date(destination: str) -> str:
    outlook = weather_service.upcoming_daily_risk(destination, days=7)
    if not outlook.get("available"):
        return ""
    # Skip today (index 0): same-day reschedules are not offered.
    for day in outlook.get("days", [])[1:]:
        if day.get("risk") == "low":
            return day.get("date", "")
    return ""


def run_fleet_watch(db: Session) -> dict:
    """One autonomous sweep over all active shipments. Returns a triage
    report for the ops console; customer notifications happen as a side
    effect."""
    shipments = (
        db.query(Shipment)
        .filter(Shipment.status.notin_(_ACTIVE_EXCLUDED))
        .all()
    )
    report = {"scanned": len(shipments), "flagged": [], "notifications_created": 0}

    for shipment in shipments:
        flags = []
        recommended_action = ""

        if shipment.status in _TROUBLE_STATUSES:
            flags.append(shipment.status)
            recommended_action = "Offer a reschedule to a firm date, or hold at a nearby FedEx facility."
            if shipment.status == "delivery_attempt_failed":
                message = (
                    f"We missed you: a delivery attempt for {shipment.tracking_id} was unsuccessful. "
                    f"Ask the assistant to redeliver on a day that suits you, or to hold it "
                    f"for pickup at a nearby FedEx location."
                )
                notification_type = "delivery_attempt"
            else:
                message = (
                    f"Heads up: our AI operations agent flagged your shipment "
                    f"{shipment.tracking_id} ({shipment.status}, currently at {shipment.location}). "
                    f"You can reschedule it or have it held for pickup — just ask the assistant."
                )
                notification_type = "delay"
            created = _notify_once(
                db, shipment.customer_id, shipment.tracking_id, notification_type, message,
            )
            report["notifications_created"] += int(created)

        weather = weather_service.destination_weather(shipment.destination, shipment.eta)
        risk = weather.get("risk") if weather.get("available") else None
        if risk in ("moderate", "high"):
            flags.append(f"weather_{risk}")
            if risk == "high" and weather.get("eta_within_forecast", True):
                better_date = _earliest_low_risk_date(shipment.destination)
                suggestion = (
                    f" The first clear day looks like {better_date} — ask the assistant to reschedule."
                    if better_date else " Ask the assistant for weather-safe delivery dates."
                )
                recommended_action = (
                    f"Reschedule to {better_date}." if better_date
                    else "Check delivery-date suggestions and reschedule."
                )
                created = _notify_once(
                    db, shipment.customer_id, shipment.tracking_id, "weather_alert",
                    f"Weather alert: {weather.get('conditions', 'severe weather')} expected near "
                    f"{weather.get('location', shipment.destination)} around "
                    f"{weather.get('forecast_date', shipment.eta)}. Your delivery "
                    f"(ETA {shipment.eta}) may be delayed.{suggestion}",
                )
                report["notifications_created"] += int(created)
            elif not recommended_action:
                recommended_action = "Monitor: weather risk is moderate at the destination."

        if flags:
            report["flagged"].append({
                "tracking_id": shipment.tracking_id,
                "customer_id": shipment.customer_id,
                "status": shipment.status,
                "destination": shipment.destination,
                "eta": shipment.eta,
                "flags": flags,
                "recommended_action": recommended_action,
            })

    logger.info(
        f"Fleet watch: scanned={report['scanned']} flagged={len(report['flagged'])} "
        f"notified={report['notifications_created']}"
    )
    return report
