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

from app.core.config import settings
from app.core.security import User
from app.db.crud import get_shipment, record_agent_event
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
        "description": "Change the delivery address for a shipment. HIGH-IMPACT (autonomy tier 3): call it as soon as you know the new address. The platform refuses the first call and shows the customer a confirmation control; ask in one short sentence and stop. Only after the customer's own next message says yes will the same call succeed.",
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
        "description": "Cancel a shipment. HIGH-IMPACT (autonomy tier 3): call it right away. The platform refuses the first call and shows the customer a confirmation control; ask in one short sentence and stop. Only after the customer's own next message says yes will the same call succeed.",
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
        "name": "set_delivery_commitment",
        "description": "Create a delivery commitment the agent becomes responsible for, when the customer states an OUTCOME with a deadline (e.g. 'I need this before Friday'). FedE's monitoring loop then watches ETA, status and weather against the target date and intervenes when the goal is at risk. Restating a deadline updates the existing commitment.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {
                    "type": "string",
                    "description": "Customer ID such as CUST001."
                },
                "tracking_id": {
                    "type": "string",
                    "description": "Tracking ID such as FX100001."
                },
                "target_date": {
                    "type": "string",
                    "description": "Deadline in YYYY-MM-DD format."
                },
                "priority": {
                    "type": "string",
                    "enum": ["low", "normal", "high"]
                }
            },
            "required": [
                "customer_id",
                "tracking_id",
                "target_date"
            ]
        }
    },
    {
        "name": "get_delivery_commitments",
        "description": "List the delivery commitments FedE is watching for a customer, including AT_RISK ones with the reason.",
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
        "name": "plan_optimal_route",
        "description": "Compute the shortest hub-to-hub route for a shipment (Dijkstra over the FedEx hub network), with live weather risk at each hub and an automatic reroute around high-risk hubs. Use when the user asks how their package will travel, why the route goes a certain way, whether weather will delay or reroute it, or how long transit will take.",
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
        "name": "save_customer_preference",
        "description": "Remember a durable customer preference for future conversations (e.g. delivery_window='after 6pm', preferred_pickup='FedEx Midtown', contact_channel='sms'). Use whenever the customer states a lasting preference, so they never have to repeat it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {
                    "type": "string",
                    "description": "Customer ID such as CUST001."
                },
                "preference": {
                    "type": "string",
                    "description": "Short snake_case preference name, e.g. delivery_window."
                },
                "value": {
                    "type": "string",
                    "description": "The preference value, e.g. 'after 6pm on weekdays'."
                }
            },
            "required": [
                "customer_id",
                "preference",
                "value"
            ]
        }
    },
    {
        "name": "get_customer_preferences",
        "description": "List the stored preferences for a customer. Use to personalize suggestions (delivery windows, pickup points) before proposing an action.",
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
        "name": "create_support_case",
        "description": "Escalate to a human FedEx representative by opening a support case. Write a concise, factual summary of the customer's issue from this conversation (what happened, what was tried, what they need). Use for complaints, damaged/lost packages, refunds, or anything the tools cannot resolve.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {
                    "type": "string",
                    "description": "Customer ID such as CUST001."
                },
                "summary": {
                    "type": "string",
                    "description": "Concise case summary written for the human rep."
                },
                "tracking_id": {
                    "type": "string",
                    "description": "Optional related tracking ID such as FX100001."
                },
                "priority": {
                    "type": "string",
                    "description": "low, normal, or high.",
                    "enum": ["low", "normal", "high"]
                }
            },
            "required": [
                "customer_id",
                "summary"
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


# ── Governance: autonomy tiers ─────────────────────────────────────
# 1 = reversible/low-risk: act silently.
# 2 = moderate: act, then inform the customer.
# 3 = hard to reverse / high impact: ask the customer first — the tool
#     REFUSES unless the model passes confirmed=true after an explicit
#     confirmation in the conversation. Policy lives here, not in the
#     prompt, so a jailbroken model still cannot skip the confirmation.
AUTONOMY_TIERS = {
    "track_shipment": 1,
    "check_weather_impact": 1,
    "suggest_delivery_dates": 1,
    "plan_optimal_route": 1,
    "list_customer_shipments": 1,
    "get_customer_notifications": 1,
    "get_customer_preferences": 1,
    "get_delivery_commitments": 1,
    "reschedule_delivery": 2,
    "hold_at_location": 2,
    "save_customer_preference": 2,
    "create_support_case": 2,
    "set_delivery_commitment": 2,
    "redirect_package": 3,
    "cancel_shipment": 3,
}

# Post-mutation verification: expected state after each mutating tool.
# Plan -> Act -> READ BACK -> compare. The agent only reports success it
# has observed, never success it merely received from a return value.
_VERIFY_CHECKS = {
    "reschedule_delivery": lambda s, args: s.eta == args.get("new_date") and s.status == "rescheduled",
    "redirect_package": lambda s, args: s.destination == args.get("new_address"),
    "cancel_shipment": lambda s, args: s.status == "cancelled",
    "hold_at_location": lambda s, args: s.status == "hold_requested",
}

# Human-readable action log lines (the activity feed's "Action" column).
_FEED_LABELS = {
    "track_shipment": "Checked shipment status",
    "check_weather_impact": "Checked weather conditions",
    "suggest_delivery_dates": "Evaluated lower-risk delivery dates",
    "plan_optimal_route": "Planned the optimal hub route",
    "list_customer_shipments": "Reviewed the customer's shipments",
    "get_customer_notifications": "Reviewed notifications",
    "get_customer_preferences": "Recalled stored preferences",
    "get_delivery_commitments": "Reviewed delivery commitments",
    "reschedule_delivery": "Rescheduled the delivery",
    "hold_at_location": "Held the shipment for pickup",
    "save_customer_preference": "Remembered a preference",
    "create_support_case": "Escalated to a human representative",
    "set_delivery_commitment": "Took on a delivery commitment",
    "redirect_package": "Redirected the package",
    "cancel_shipment": "Cancelled the shipment",
}


def _dispatch(name: str, arguments: dict, db: Session, user: User):
    if name == "track_shipment":
        return shipment_ops.track(db, user, arguments["tracking_id"])
    if name == "reschedule_delivery":
        return shipment_ops.reschedule(db, user, arguments["tracking_id"], arguments["new_date"])
    if name == "redirect_package":
        return shipment_ops.redirect(db, user, arguments["tracking_id"], arguments["new_address"])
    if name == "cancel_shipment":
        return shipment_ops.cancel(db, user, arguments["tracking_id"], arguments.get("reason", ""))
    if name == "list_customer_shipments":
        return shipment_ops.list_for_customer(db, user, arguments["customer_id"])
    if name == "check_weather_impact":
        return shipment_ops.weather_impact(db, user, arguments["tracking_id"])
    if name == "suggest_delivery_dates":
        return shipment_ops.suggest_delivery_dates(db, user, arguments["tracking_id"])
    if name == "hold_at_location":
        return shipment_ops.hold_at_location(db, user, arguments["tracking_id"], arguments.get("location", ""))
    if name == "get_customer_notifications":
        return shipment_ops.notifications_for(db, user, arguments["customer_id"])
    if name == "plan_optimal_route":
        return shipment_ops.journey_for(db, user, arguments["tracking_id"])
    if name == "save_customer_preference":
        return shipment_ops.save_preference(
            db, user, arguments["customer_id"], arguments["preference"], arguments["value"])
    if name == "get_customer_preferences":
        return shipment_ops.preferences_for(db, user, arguments["customer_id"])
    if name == "create_support_case":
        return shipment_ops.open_support_case(
            db, user, arguments["customer_id"], arguments["summary"],
            arguments.get("tracking_id", ""), arguments.get("priority", "normal"))
    if name == "set_delivery_commitment":
        return shipment_ops.set_delivery_commitment(
            db, user, arguments["customer_id"], arguments["tracking_id"],
            arguments["target_date"], arguments.get("priority", "normal"))
    if name == "get_delivery_commitments":
        return shipment_ops.commitments_for(db, user, arguments["customer_id"])
    raise KeyError(f"unknown tool {name}")


def execute_tool(name: str, arguments: dict, db: Session, user: User, provider: str = "",
                 confirmed_by_customer: bool = False) -> dict:
    """
    Execute one tool call through the governance pipeline:
    tier check -> execute -> verify (read back) -> audit log.
    Authorization and business-rule failures are returned as
    ``{"error": ...}`` so the model can relay them politely instead of
    the request 500-ing.

    ``confirmed_by_customer`` is set by the platform (see
    ``confirmations.resolve``) when the customer's own message confirmed
    this exact action. Anything the model puts in ``arguments`` (such as a
    ``confirmed`` flag) is ignored for the tier-3 gate.
    """
    arguments = {k: v for k, v in (arguments or {}).items() if k != "confirmed"}
    if name not in AUTONOMY_TIERS:
        return {"ok": False, "error": f"Unknown tool: {name}"}
    tier = AUTONOMY_TIERS[name]
    tracking_id = str(arguments.get("tracking_id", "") or "")

    # Snapshot before the action: verification target + cost attribution.
    prior_status = ""
    prior_customer = ""
    if tracking_id:
        prior = get_shipment(db, tracking_id)
        if prior is not None:
            prior_status = prior.status
            prior_customer = prior.customer_id or ""
    customer_id = str(arguments.get("customer_id", "") or "") or prior_customer or (user.customer_id or "")

    def _log(ok: bool, note: str, cost: float = 0.0):
        record_agent_event(db, user.username, user.role, name, ok, provider,
                           tracking_id, customer_id, note, cost)

    # Tier-3 gate: only the customer's own confirmation, held by the
    # platform, opens it. The model cannot grant it to itself.
    if tier >= 3 and not confirmed_by_customer:
        _log(False, "Blocked pending customer confirmation (autonomy tier 3)")
        return {
            "ok": False,
            "requires_confirmation": True,
            "autonomy_tier": tier,
            "error": (f"{name} is a high-impact action (autonomy tier 3). The customer has not "
                      "confirmed it yet. Ask them in one short sentence and stop; do not call "
                      "this tool again in this turn. It will succeed once their next message says yes."),
        }

    try:
        result = _dispatch(name, arguments, db, user)
    except HTTPException as exc:
        _log(False, f"Refused: {exc.detail}")
        return {"ok": False, "error": exc.detail}
    except KeyError as exc:
        _log(False, f"Missing required argument: {exc}")
        return {"ok": False, "error": f"Missing required argument: {exc}"}
    except Exception as exc:  # never leak a stack trace to the model / user
        _log(False, f"Tool execution failed: {exc}")
        return {"ok": False, "error": f"Tool execution failed: {exc}"}

    # Verification: re-read the shipment and compare expected vs actual.
    check = _VERIFY_CHECKS.get(name)
    if check is not None and tracking_id:
        db.expire_all()
        actual = get_shipment(db, tracking_id)
        if actual is None or not check(actual, arguments):
            _log(False, "Post-action verification FAILED: state did not match the expected outcome")
            return {
                "ok": False,
                "verified": False,
                "error": (f"{name} did not verify: the shipment state after the action does not "
                          "match the expected outcome. Tell the customer honestly that the change "
                          "was not applied, and do not claim success."),
            }
        result["verified"] = True

    # Cost attribution: a hold/reschedule that rescues a failed-attempt
    # shipment avoids ~one more failed attempt (sourced benchmark).
    cost_avoided = 0.0
    if name in ("hold_at_location", "reschedule_delivery") and prior_status == "delivery_attempt_failed":
        cost_avoided = settings.FAILED_ATTEMPT_COST
        result["estimated_cost_avoided_usd"] = cost_avoided
        result["cost_basis"] = (
            f"Avoids ~1 failed delivery attempt at the ${settings.FAILED_ATTEMPT_COST} "
            "industry benchmark; other operational costs are not invented."
        )

    note = _FEED_LABELS.get(name, name)
    if tracking_id:
        note += f" ({tracking_id})"
    if result.get("verified"):
        note += " and verified the new state"
    _log(True, note, cost_avoided)
    return {"ok": True, "result": result}
