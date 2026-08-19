"""
Planner agent.

Primary path (when an Anthropic key is configured): a genuine agentic loop.
The model is given typed tools (see ``agent_tools``) and *plans* — it decides
which tools to call and with what arguments, we execute the real operation
(with authorization), feed results back, and let it call more tools or answer.
This means "reschedule FX100001 to next Friday" actually reschedules the
shipment, rather than handing the user back an API URL to call themselves.

Fallback path (no key / offline demo): a deterministic keyword+regex router
so tracking and guidance still work with zero external dependencies. Mutating
actions in fallback mode return guidance instead of guessing structured
arguments from free text.
"""
import json
import re

from sqlalchemy.orm import Session

from app.agents.agent_tools import TOOL_SCHEMAS, execute_tool
from app.core.config import settings
from app.core.logger import get_logger
from app.core.security import User
from app.services import conversation_store, shipment_ops
from app.services.llm_service import get_llm_client

logger = get_logger()

_MAX_TOOL_ROUNDS = 5

_SYSTEM_PROMPT = (
    "You are FedE, FedEx's proactive customer-support assistant. You help "
    "customers track, reschedule, redirect, and cancel shipments, list their "
    "packages, and review notifications. Use the provided tools to take real "
    "actions rather than describing how the user could do it themselves. "
    "Be PROACTIVE: when a shipment is delayed or has an exception, offer the "
    "relevant next step (e.g. reschedule or redirect) in the same reply. Use "
    "check_weather_impact when the user asks about weather, and proactively "
    "when a shipment is delayed or in transit, so you can warn about high "
    "weather risk at the destination and offer to reschedule. When weather "
    "risk is high, chain suggest_delivery_dates to propose the earliest "
    "low-risk date, or offer hold_at_location for pickup instead. When "
    "the user refers to their packages without a tracking ID, use "
    "list_customer_shipments to find them. Always confirm a destructive action "
    "(cancellation) before performing it. If a tool returns an error (e.g. not "
    "authorized, or a shipment cannot be cancelled), explain it plainly and do "
    "not retry the same call. Use conversation history to resolve references "
    "like 'it' or 'that one'. Keep replies concise and friendly. Tracking IDs "
    "look like FX100001."
)


def _empty_usage() -> dict:
    return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "model_calls": 0}


def _add_usage(total: dict, response) -> None:
    """Accumulate real token usage reported by the API across the tool loop."""
    total["model_calls"] += 1
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    prompt = (
        (getattr(usage, "input_tokens", 0) or 0)
        + (getattr(usage, "cache_read_input_tokens", 0) or 0)
        + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
    )
    completion = getattr(usage, "output_tokens", 0) or 0
    total["prompt_tokens"] += prompt
    total["completion_tokens"] += completion
    total["total_tokens"] += prompt + completion


def _run_agentic(query: str, db: Session, user: User, history: list) -> dict:
    """Tool-calling loop against the Anthropic Messages API."""
    client = get_llm_client()
    system = (
        _SYSTEM_PROMPT
        + f" The authenticated caller has role='{user.role}', customer_id={user.customer_id}."
    )
    messages = list(history)  # prior turns for multi-turn context
    messages.append({"role": "user", "content": query})
    actions_taken = []
    structured_result = None
    usage = _empty_usage()

    for _ in range(_MAX_TOOL_ROUNDS):
        response = client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=settings.ANTHROPIC_MAX_TOKENS,
            system=system,
            messages=messages,
            tools=TOOL_SCHEMAS,
        )
        _add_usage(usage, response)
        tool_uses = [b for b in response.content if getattr(b, "type", "") == "tool_use"]
        text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")

        if not tool_uses:
            return {
                "intent": "agentic",
                "actions_taken": actions_taken,
                "data": structured_result or {},
                "response": text,
                "usage": usage,
            }

        # Record the assistant turn (with its tool-use blocks) verbatim.
        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for tu in tool_uses:
            name = tu.name
            args = dict(tu.input or {})
            logger.info(f"Agent tool call: {name}({args}) as {user.role}")
            outcome = execute_tool(name, args, db, user)
            actions_taken.append({"tool": name, "arguments": args, "ok": outcome.get("ok", False)})
            if outcome.get("ok") and "result" in outcome:
                structured_result = outcome["result"]
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": json.dumps(outcome, default=str),
                    "is_error": not outcome.get("ok", False),
                }
            )
        # All tool results for this turn go back in a single user message.
        messages.append({"role": "user", "content": tool_results})

    logger.warning("Agentic loop hit max tool rounds without a final answer.")
    return {
        "intent": "agentic",
        "actions_taken": actions_taken,
        "data": structured_result or {},
        "response": "I've taken the actions I could. Please let me know if you need anything else.",
        "usage": usage,
    }


# ── Deterministic fallback (no Anthropic key) ──────────────────────

def _extract_tracking_id(query: str) -> str:
    m = re.search(r"FX\d+", query, re.IGNORECASE)
    if m:
        return m.group(0).upper()
    m = re.search(r"\d{4,}", query)
    return m.group(0) if m else ""


def _detect_intent(query: str) -> str:
    q = query.lower()
    buckets = {
        "reschedule": ["reschedule", "change date", "change delivery", "postpone"],
        "redirect": ["redirect", "change address", "different address", "send to"],
        "cancel": ["cancel", "stop shipment", "stop delivery"],
        "hold": ["hold", "pickup point", "pick it up", "not home", "collect it"],
        "notification": ["notify", "notification", "alert", "update me", "status update"],
        "weather": ["weather", "rain", "snow", "storm", "forecast", "wind"],
        "track": ["track", "where is", "shipment", "package", "delivery status", "locate", "find my"],
    }
    for intent, kws in buckets.items():
        if any(kw in q for kw in kws):
            return intent
    if re.search(r"\d{4,}", q):
        return "track"
    return "general"


def _run_fallback(query: str, db: Session, user: User) -> dict:
    intent = _detect_intent(query)
    logger.info(f"[fallback] Query: '{query}' | intent={intent}")

    if intent == "track":
        tracking_id = _extract_tracking_id(query)
        if not tracking_id:
            return {"intent": "track", "data": {"message": "No tracking ID found in your query."}}
        # Try as-is, then with an FX prefix for bare digits.
        candidates = [tracking_id]
        if not tracking_id.upper().startswith("FX"):
            candidates.append(f"FX{tracking_id}")
        for candidate in candidates:
            try:
                result = shipment_ops.track(db, user, candidate)
                return {"intent": "track", "data": result}
            except Exception:
                continue
        return {"intent": "track", "data": {"message": f"No shipment found for {tracking_id}"}}

    if intent == "weather":
        tracking_id = _extract_tracking_id(query)
        if not tracking_id:
            return {"intent": "weather",
                    "data": {"message": "Give me a tracking ID (e.g. FX100001) and I'll check the forecast at its destination."}}
        candidates = [tracking_id]
        if not tracking_id.upper().startswith("FX"):
            candidates.append(f"FX{tracking_id}")
        for candidate in candidates:
            try:
                result = shipment_ops.weather_impact(db, user, candidate)
                return {"intent": "weather", "data": result}
            except Exception:
                continue
        return {"intent": "weather", "data": {"message": f"No shipment found for {tracking_id}"}}

    guidance = {
        "reschedule": "To reschedule, use POST /reschedule with your tracking_id and new_date.",
        "redirect": "To redirect, use POST /redirect with your tracking_id and new_address.",
        "cancel": "To cancel, use POST /cancel with your tracking_id.",
        "hold": "To hold your package for pickup, use POST /hold with your tracking_id (and an optional location).",
        "notification": "To view notifications, use GET /notifications/{customer_id}.",
    }
    if intent in guidance:
        return {"intent": intent, "data": {"message": guidance[intent]}}

    return {
        "intent": "general",
        "data": {"response": "AI assistant is offline (no Anthropic key configured). "
                             "I can still track shipments — try 'track FX100001'."},
    }


def handle_user_query(query: str, db: Session, user: User, session_id: str = "") -> dict:
    """
    Entry point used by the /ask and /voice endpoints. Chooses the agentic
    path when possible, otherwise degrades gracefully. When a ``session_id`` is
    supplied, prior turns are replayed for multi-turn context and this turn is
    persisted afterwards.
    """
    try:
        if get_llm_client() is not None:
            history = conversation_store.get_history(session_id)
            result = _run_agentic(query, db, user, history)
            # Persist only the final user/assistant text — not tool scaffolding.
            conversation_store.append_turn(session_id, query, result.get("response", ""))
            return result
        result = _run_fallback(query, db, user)
        result.setdefault("usage", _empty_usage())
        return result
    except Exception as exc:
        logger.error(f"PlannerAgent error: {exc}")
        # Last-resort graceful degradation — never surface a stack trace.
        try:
            result = _run_fallback(query, db, user)
            result.setdefault("usage", _empty_usage())
            return result
        except Exception:
            return {"intent": "error", "data": {"error": "Sorry, I hit an unexpected problem handling that."},
                    "usage": _empty_usage()}
