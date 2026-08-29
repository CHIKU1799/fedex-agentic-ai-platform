"""
Agentic analytics: what did the AI actually do?

Aggregates the agent_events audit trail plus the business tables into one
ops-dashboard payload: action volumes per tool, success rate, which brain
served the requests (Anthropic / Groq / offline router), proactive
notification counts by type, open support cases, and fleet health by
shipment status.
"""
from collections import Counter

from sqlalchemy.orm import Session

from app.db.crud import list_agent_events
from app.db.models import (
    CustomerPreference, DeliveryCommitment, Notification, Shipment, SupportCase,
)


def agent_analytics(db: Session) -> dict:
    events = list_agent_events(db)

    tool_counter = Counter()
    tool_ok = Counter()
    provider_counter = Counter()
    ok_total = 0
    for e in events:
        tool_counter[e.tool] += 1
        provider_counter[e.provider or "unknown"] += 1
        if e.ok == "true":
            tool_ok[e.tool] += 1
            ok_total += 1

    cases = db.query(SupportCase).all()
    notifications = db.query(Notification).all()
    shipments = db.query(Shipment).all()
    commitments = db.query(DeliveryCommitment).all()

    return {
        "totals": {
            "agent_actions": len(events),
            "success_rate": round(ok_total / len(events), 3) if events else None,
            "cases_total": len(cases),
            "cases_open": sum(1 for c in cases if c.status == "open"),
            "notifications_sent": len(notifications),
            "preferences_stored": db.query(CustomerPreference).count(),
            "cost_avoided_usd": round(sum(e.cost_avoided or 0.0 for e in events), 2),
        },
        "commitments": {
            "total": len(commitments),
            "by_status": dict(Counter(c.status for c in commitments)),
            "at_risk": [
                {
                    "commitment_id": c.id,
                    "tracking_id": c.tracking_id,
                    "customer_id": c.customer_id,
                    "target_date": c.target_date,
                    "risk_reason": c.risk_reason,
                }
                for c in commitments if c.status == "AT_RISK"
            ],
        },
        "tool_usage": [
            {"tool": tool, "count": count, "ok": tool_ok.get(tool, 0)}
            for tool, count in tool_counter.most_common()
        ],
        "provider_usage": dict(provider_counter),
        "notifications_by_type": dict(Counter(n.notification_type for n in notifications)),
        "shipments_by_status": dict(Counter(s.status for s in shipments)),
        "recent_actions": [
            {
                "tool": e.tool,
                "ok": e.ok == "true",
                "role": e.role,
                "provider": e.provider,
                "note": e.note,
                "tracking_id": e.tracking_id,
                "cost_avoided": e.cost_avoided or 0.0,
                "at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in events[:12]
        ],
    }
