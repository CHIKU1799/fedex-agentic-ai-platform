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

from app.core.config import settings
from app.core.logger import get_logger
from app.db.crud import (
    get_shipment, list_commitments, notify_once, record_agent_event, set_commitment_status,
)
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

    _evaluate_commitments(db, report)

    record_agent_event(
        db, "fede-watch", "system", "fleet_watch_scan", True, "fleet_watch",
        note=(f"Autonomous sweep: {report['scanned']} shipments scanned, "
              f"{len(report['flagged'])} flagged, "
              f"{report['notifications_created']} customers notified"),
    )
    logger.info(
        f"Fleet watch: scanned={report['scanned']} flagged={len(report['flagged'])} "
        f"notified={report['notifications_created']} commitments={report['commitments']}"
    )
    return report


def _evaluate_commitments(db: Session, report: dict) -> None:
    """
    Goal-driven monitoring: re-evaluate every active commitment against the
    live shipment state. Customer-first policy: the recommendation is chosen
    for the customer's outcome (make the deadline, avoid another missed
    attempt); any cost avoided is reported as a byproduct, never used to
    pick the option.
    """
    summary = {"active": 0, "at_risk": 0, "met": 0, "details": []}
    for c in list_commitments(db, active_only=True):
        shipment = get_shipment(db, c.tracking_id)
        if shipment is None:
            continue

        if shipment.status == "delivered":
            set_commitment_status(db, c.id, "MET")
            summary["met"] += 1
            notify_once(
                db, c.customer_id, c.tracking_id,
                f"Goal met: {c.tracking_id} was delivered within your {c.target_date} commitment "
                f"({c.id}).",
                "commitment_update",
            )
            record_agent_event(db, "fede-watch", "system", "commitment_met", True, "fleet_watch",
                               c.tracking_id, c.customer_id,
                               f"Commitment {c.id} met: delivered within the {c.target_date} target")
            continue

        reasons = []
        if shipment.eta and shipment.eta > c.target_date:
            reasons.append(f"current ETA {shipment.eta} is after your {c.target_date} deadline")
        if shipment.status in _TROUBLE_STATUSES:
            reasons.append(f"the shipment is {shipment.status.replace('_', ' ')}")
        weather = weather_service.destination_weather(shipment.destination, shipment.eta)
        if (weather.get("available") and weather.get("risk") == "high"
                and weather.get("eta_within_forecast", True)):
            reasons.append(f"high weather risk near {shipment.destination}")

        if not reasons:
            if c.status == "AT_RISK":
                # Risk changed back: send the updated status, don't stay silent.
                set_commitment_status(db, c.id, "ACTIVE", "")
                notify_once(
                    db, c.customer_id, c.tracking_id,
                    f"Good news: {c.tracking_id} is back on track for your {c.target_date} "
                    f"commitment ({c.id}).",
                    "commitment_update",
                )
                record_agent_event(db, "fede-watch", "system", "commitment_recovered", True,
                                   "fleet_watch", c.tracking_id, c.customer_id,
                                   f"Commitment {c.id} back on track for {c.target_date}")
            summary["active"] += 1
            continue

        # Goal at risk: pick the fix that best serves the CUSTOMER.
        reason = "; ".join(reasons)
        set_commitment_status(db, c.id, "AT_RISK", reason)
        summary["at_risk"] += 1
        cost_avoided = 0.0
        if shipment.status == "delivery_attempt_failed":
            recommendation = ("hold it for pickup at a nearby FedEx location (you get it sooner "
                              "and skip another missed attempt), or reschedule to a day you'll be home")
            cost_avoided = settings.FAILED_ATTEMPT_COST
        else:
            better = _earliest_low_risk_date(shipment.destination)
            recommendation = (
                f"reschedule to {better}, the first low-risk day"
                if better and better <= c.target_date
                else "reschedule or hold for pickup; ask the assistant for the best option"
            )
        created = notify_once(
            db, c.customer_id, c.tracking_id,
            f"Your {c.target_date} delivery goal for {c.tracking_id} ({c.id}) is at risk: "
            f"{reason}. Recommended: {recommendation}.",
            "commitment_risk",
        )
        report["notifications_created"] += int(created is not None)
        record_agent_event(db, "fede-watch", "system", "commitment_risk_detected", True,
                           "fleet_watch", c.tracking_id, c.customer_id,
                           f"Commitment {c.id} at risk ({reason}); recommended: {recommendation}",
                           cost_avoided)
        summary["details"].append({
            "commitment_id": c.id,
            "tracking_id": c.tracking_id,
            "customer_id": c.customer_id,
            "target_date": c.target_date,
            "risk_reason": reason,
            "recommendation": recommendation,
            "estimated_cost_avoided_usd": cost_avoided,
        })
    report["commitments"] = summary
