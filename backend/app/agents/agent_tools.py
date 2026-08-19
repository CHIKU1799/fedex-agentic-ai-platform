"""
Tool definitions and dispatch for the agentic planner.

This is what makes the planner *agentic* rather than a keyword router: the
LLM is given a set of typed tools, decides which to call (and with what
arguments) based on the conversation, and we execute the real business
operation — including authorization — then feed the result back so the model
can plan its next step or answer. Every tool routes through
``app.services.shipment_ops`` so rules and access control are identical to
the REST API.
"""
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.security import User
from app.services import shipment_ops

# Anthropic tool-use schemas.
TOOL_SCHEMAS = [
    {
        "name": "track_shipment",
        "description": "Look up the current status, location and ETA of a shipment by its tracking ID (e.g. FX100001).",
        "input_schema": {
            "type": "object",
            "properties": {
                "tracking_id": {
                    "type": "string",
                    "description": "Tracking ID such as FX100001."
                }
            },
            "required": [
                "tracking_id"
            ]
        }
    },
    {
        "name": "reschedule_delivery",
        "description": "Change the delivery date for a shipment. Use only when the customer clearly asked to reschedule and gave a date.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tracking_id": {
                    "type": "string"
                },
                "new_date": {
                    "type": "string",
                    "description": "New delivery date in YYYY-MM-DD format."
                }
            },
            "required": [
                "tracking_id",
                "new_date"
            ]
        }
    },
    {
        "name": "redirect_package",
        "description": "Change the delivery address for a shipment.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tracking_id": {
                    "type": "string"
                },
                "new_address": {
                    "type": "string",
                    "description": "Full new delivery address."
                }
            },
            "required": [
                "tracking_id",
                "new_address"
            ]
        }
    },
    {
        "name": "cancel_shipment",
        "description": "Cancel a shipment. Confirm intent from the user before calling.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tracking_id": {
                    "type": "string"
                },
                "reason": {
                    "type": "string",
                    "description": "Optional cancellation reason."
                }
            },
            "required": [
                "tracking_id"
            ]
        }
    },
    {
        "name": "list_customer_shipments",
        "description": "List all shipments belonging to a customer ID. Use for 'what are all my packages?' or to find a shipment when the user doesn't give a tracking ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {
                    "type": "string",
                    "description": "Customer ID such as CUST001."
                }
            },
            "required": [
                "customer_id"
            ]
        }
    },
    {
        "name": "check_weather_impact",
        "description": "Check the weather forecast at a shipment's destination and assess delivery-delay risk (low/moderate/high) around its ETA. Use when the user asks about weather, or proactively when a shipment is delayed or in transit and weather could explain or threaten the ETA. High risk automatically notifies the customer.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tracking_id": {
                    "type": "string",
                    "description": "Tracking ID such as FX100001."
                }
            },
            "required": [
                "tracking_id"
            ]
        }
    },
    {
        "name": "suggest_delivery_dates",
        "description": "Get weather-aware delivery-date suggestions for a shipment: a 7-day risk outlook at the destination plus the earliest low-risk date. Use before rescheduling around bad weather, or when the user asks 'when should I get it delivered instead?'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tracking_id": {
                    "type": "string",
                    "description": "Tracking ID such as FX100001."
                }
            },
            "required": [
                "tracking_id"
            ]
        }
    },
    {
        "name": "hold_at_location",
        "description": "Hold the package for pickup at a FedEx facility instead of delivering it. Use when the customer will not be home, or as a safe option when weather risk at the destination is high. Confirm intent before calling.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tracking_id": {
                    "type": "string"
                },
                "location": {
                    "type": "string",
                    "description": "Optional preferred pickup location; defaults to the facility nearest the destination."
                }
            },
            "required": [
                "tracking_id"
            ]
        }
    },
    {
        "name": "get_customer_notifications",
        "description": "List proactive notifications (delays, ETA updates, exceptions) for a customer ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {
                    "type": "string",
                    "description": "Customer ID such as CUST001."
                }
            },
            "required": [
                "customer_id"
            ]
        }
    }
]


def execute_tool(name: str, arguments: dict, db: Session, user: User) -> dict:
    """
    Execute one tool call. Returns a JSON-serializable dict. Authorization
    and business-rule failures are returned as ``{"error": ...}`` so the
    model can relay them politely instead of the request 500-ing.
    """
    try:
        if name == "track_shipment":
            return {"ok": True, "result": shipment_ops.track(db, user, arguments["tracking_id"])}
        if name == "reschedule_delivery":
            return {"ok": True, "result": shipment_ops.reschedule(db, user, arguments["tracking_id"], arguments["new_date"])}
        if name == "redirect_package":
            return {"ok": True, "result": shipment_ops.redirect(db, user, arguments["tracking_id"], arguments["new_address"])}
        if name == "cancel_shipment":
            return {"ok": True, "result": shipment_ops.cancel(db, user, arguments["tracking_id"], arguments.get("reason", ""))}
        if name == "list_customer_shipments":
            return {"ok": True, "result": shipment_ops.list_for_customer(db, user, arguments["customer_id"])}
        if name == "check_weather_impact":
            return {"ok": True, "result": shipment_ops.weather_impact(db, user, arguments["tracking_id"])}
        if name == "suggest_delivery_dates":
            return {"ok": True, "result": shipment_ops.suggest_delivery_dates(db, user, arguments["tracking_id"])}
        if name == "hold_at_location":
            return {"ok": True, "result": shipment_ops.hold_at_location(db, user, arguments["tracking_id"], arguments.get("location", ""))}
        if name == "get_customer_notifications":
            return {"ok": True, "result": shipment_ops.notifications_for(db, user, arguments["customer_id"])}
        return {"ok": False, "error": f"Unknown tool: {name}"}
    except HTTPException as exc:
        return {"ok": False, "error": exc.detail}
    except KeyError as exc:
        return {"ok": False, "error": f"Missing required argument: {exc}"}
    except Exception as exc:  # never leak a stack trace to the model / user
        return {"ok": False, "error": f"Tool execution failed: {exc}"}
