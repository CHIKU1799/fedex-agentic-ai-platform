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
from app.db.crud import get_preferences
from app.services import conversation_store, llm_service, shipment_ops
from app.services.llm_service import AnthropicProvider, GroqProvider, ProviderBillingError, get_llm_client

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
    "look like FX100001. You have durable memory: when the customer states a "
    "lasting preference (delivery window, preferred pickup point, contact "
    "channel), call save_customer_preference so they never repeat it, and use "
    "any known preferences to personalize dates and options you propose. When "
    "the issue is beyond your tools (damaged or lost package, refund, "
    "complaint, or the customer asks for a human), call create_support_case "
    "with a concise factual summary of the conversation so a FedEx "
    "representative can take over with full context, and give the customer "
    "their case ID. If the customer sounds frustrated, angry, or repeatedly "
    "blocked, acknowledge it, and offer to open a support case (priority "
    "'high' when the issue is urgent) rather than making them ask. When a "
    "shipment status is delivery_attempt_failed, lead with an apology that "
    "the driver missed them and immediately offer the two fixes: redeliver "
    "on a chosen date, or hold for pickup. Use plan_optimal_route when the "
    "user asks how their package will travel, why it goes a certain way, or "
    "whether weather will reroute it — it computes the shortest hub path "
    "and a weather-adjusted alternative you can explain. "
    "GOALS: when the customer states an OUTCOME with a deadline ('I need "
    "this before Friday'), call set_delivery_commitment — FedE then owns "
    "that goal and its monitoring loop watches ETA, status and weather "
    "against the target date, intervening the moment it is at risk. "
    "COST RULES, customer-first: customer satisfaction ALWAYS outranks "
    "saving the company money. (1) a customer commitment or preference "
    "always beats cost savings; never pick a cheaper option that misses "
    "their deadline or degrades their experience. (2) recommend "
    "cost-saving options like hold_at_location only when the customer "
    "clearly benefits too (gets it sooner, avoids another missed attempt); "
    "if an option only helps FedEx, do not push it. Cost avoided is a "
    "byproduct to report, never the reason to choose. (3) a "
    "recommendation is not permission: high-impact actions (redirect, "
    "cancel) are autonomy tier 3 and the tools will refuse until you ask "
    "the customer and retry with confirmed=true. Tools verify mutations by "
    "re-reading state; if a tool reports verification failed, tell the "
    "customer honestly that the change was not applied."
)


def _empty_usage() -> dict:
    return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "model_calls": 0}


def _add_usage(total: dict, turn) -> None:
    """Accumulate real token usage reported by the API across the tool loop."""
    total["model_calls"] += 1
    total["prompt_tokens"] += turn.prompt_tokens
    total["completion_tokens"] += turn.completion_tokens
    total["total_tokens"] += turn.prompt_tokens + turn.completion_tokens


def _provider_chain() -> list:
    """Configured, non-demoted providers in preference order.

    Anthropic availability goes through ``get_llm_client()`` (tests patch it
    on this module); Groq needs only its API key. A provider demoted at
    runtime (billing/credential failure) is skipped for this process.
    """
    chain = []
    pref = settings.LLM_PROVIDER
    client = get_llm_client()
    if client is not None and pref in ("auto", "anthropic") and not llm_service.is_demoted("anthropic"):
        chain.append(AnthropicProvider(client))
    if settings.GROQ_API_KEY and pref in ("auto", "groq") and not llm_service.is_demoted("groq"):
        chain.append(GroqProvider())
    return chain


def _preferences_context(db: Session, user: User) -> str:
    """Durable memory injection: stored preferences ride along in the system
    prompt so the agent personalizes without re-asking every session."""
    if not user.customer_id:
        return ""
    try:
        rows = get_preferences(db, user.customer_id)
    except Exception:
        return ""
    if not rows:
        return ""
    rendered = "; ".join(f"{r.preference}={r.value}" for r in rows)
    return f" Known preferences for this customer (use them when proposing options): {rendered}."


def _run_agentic(provider, query: str, db: Session, user: User, history: list) -> dict:
    """Provider-agnostic tool-calling loop (Anthropic or Groq)."""
    system = (
        _SYSTEM_PROMPT
        + f" The authenticated caller has role='{user.role}', customer_id={user.customer_id}."
        + _preferences_context(db, user)
    )
    messages = list(history)  # prior turns for multi-turn context
    messages.append({"role": "user", "content": query})
    actions_taken = []
    structured_result = None
    usage = _empty_usage()

    for _ in range(_MAX_TOOL_ROUNDS):
        turn = provider.chat(system, messages, TOOL_SCHEMAS)
        _add_usage(usage, turn)

        if not turn.tool_calls:
            return {
                "intent": "agentic",
                "actions_taken": actions_taken,
                "data": structured_result or {},
                "response": turn.text,
                "llm_provider": provider.name,
                "usage": usage,
            }

        messages.extend(provider.assistant_messages(turn))

        tool_results = []
        for call in turn.tool_calls:
            logger.info(f"Agent tool call: {call.name}({call.args}) as {user.role}")
            outcome = execute_tool(call.name, call.args, db, user, provider=provider.name)
            actions_taken.append({"tool": call.name, "arguments": call.args, "ok": outcome.get("ok", False)})
            if outcome.get("ok") and "result" in outcome:
                structured_result = outcome["result"]
            tool_results.append(
                {
                    "id": call.id,
                    "content": json.dumps(outcome, default=str),
                    "is_error": not outcome.get("ok", False),
                }
            )
        messages.extend(provider.tool_result_messages(tool_results))

    logger.warning("Agentic loop hit max tool rounds without a final answer.")
    return {
        "intent": "agentic",
        "actions_taken": actions_taken,
        "data": structured_result or {},
        "response": "I've taken the actions I could. Please let me know if you need anything else.",
        "llm_provider": provider.name,
        "usage": usage,
    }


# ── Deterministic fallback (no Anthropic key) ──────────────────────

def _extract_tracking_id(query: str) -> str:
    m = re.search(r"FX\d+", query, re.IGNORECASE)
    if m:
        return m.group(0).upper()
    m = re.search(r"\d{4,}", query)
    return m.group(0) if m else ""


_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _extract_deadline(query: str) -> str:
    """Deterministic deadline parsing for the offline router: an ISO date,
    'tomorrow', or the next occurrence of a weekday name."""
    from datetime import date, timedelta

    q = query.lower()
    m = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", q)
    if m:
        return m.group(1)
    if "tomorrow" in q:
        return (date.today() + timedelta(days=1)).isoformat()
    for i, day in enumerate(_WEEKDAYS):
        if day in q:
            ahead = (i - date.today().weekday()) % 7 or 7
            return (date.today() + timedelta(days=ahead)).isoformat()
    return ""


def _detect_intent(query: str) -> str:
    q = query.lower()
    # Outcome + deadline ("I need FX100001 before Friday") beats every
    # keyword bucket: it becomes a monitored commitment.
    if (re.search(r"\b(by|before|until)\b", q)
            and re.search(r"\b(need|deliver|delivered|arrive|arrives|must|latest)\b", q)
            and _extract_deadline(q)):
        return "commitment"
    buckets = {
        "escalate": ["complain", "complaint", "damaged", "broken", "missing item", "lost package",
                     "refund", "escalate", "human agent", "speak to a person", "talk to a person",
                     "support case", "representative"],
        "delivery_options": ["suggest", "delivery dates", "delivery options", "best date", "which day", "better date"],
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

    if intent == "delivery_options":
        tracking_id = _extract_tracking_id(query)
        if not tracking_id:
            return {"intent": "delivery_options",
                    "data": {"message": "Give me a tracking ID (e.g. FX100001) and I'll suggest weather-safe delivery dates."}}
        candidates = [tracking_id]
        if not tracking_id.upper().startswith("FX"):
            candidates.append(f"FX{tracking_id}")
        for candidate in candidates:
            try:
                result = shipment_ops.suggest_delivery_dates(db, user, candidate)
                return {"intent": "delivery_options", "data": result}
            except Exception:
                continue
        return {"intent": "delivery_options", "data": {"message": f"Could not suggest dates for {tracking_id}"}}

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

    if intent == "commitment":
        tracking_id = _extract_tracking_id(query)
        deadline = _extract_deadline(query)
        if not tracking_id:
            return {"intent": "commitment",
                    "data": {"message": "Tell me the tracking ID (e.g. FX100001) and I'll commit to that deadline."}}
        if not tracking_id.upper().startswith("FX"):
            tracking_id = f"FX{tracking_id}"
        if not user.customer_id:
            return {"intent": "commitment",
                    "data": {"message": "Please log in so I can take on that delivery commitment for you."}}
        outcome = execute_tool(
            "set_delivery_commitment",
            {"customer_id": user.customer_id, "tracking_id": tracking_id, "target_date": deadline},
            db, user, provider="offline")
        if outcome.get("ok"):
            result = outcome["result"]
            result["message"] = (
                f"Commitment {result['commitment_id']} created: I'm now watching {tracking_id} "
                f"to arrive by {deadline}. {result['note']}"
            )
            return {"intent": "commitment", "data": result}
        return {"intent": "commitment",
                "data": {"message": f"Could not create a commitment for {tracking_id}: "
                                    f"{outcome.get('error', 'unknown error')}"}}

    if intent == "hold":
        # Hold needs no structured arguments beyond the tracking ID, so the
        # offline router can execute it for real, like track/weather do.
        tracking_id = _extract_tracking_id(query)
        if not tracking_id:
            return {"intent": "hold",
                    "data": {"message": "Give me a tracking ID (e.g. FX100001) and I'll arrange a hold for pickup."}}
        candidates = [tracking_id]
        if not tracking_id.upper().startswith("FX"):
            candidates.append(f"FX{tracking_id}")
        for candidate in candidates:
            # Through the governance pipeline: tier check, verification,
            # audit log — identical to the agentic path.
            outcome = execute_tool("hold_at_location", {"tracking_id": candidate},
                                   db, user, provider="offline")
            if outcome.get("ok"):
                result = outcome["result"]
                result["message"] = f"Your package will be held for pickup at {result['pickup_location']}."
                return {"intent": "hold", "data": result}
        return {"intent": "hold",
                "data": {"message": f"Could not arrange a hold for {tracking_id}. "
                                    "Please log in, or check the tracking ID."}}

    if intent == "escalate":
        # Even fully offline, escalation to a human still works: the raw
        # query becomes the case summary for an authenticated customer.
        if user.customer_id:
            tracking_id = _extract_tracking_id(query)
            if tracking_id and not tracking_id.upper().startswith("FX"):
                tracking_id = f"FX{tracking_id}"
            outcome = execute_tool(
                "create_support_case",
                {"customer_id": user.customer_id, "summary": f"Customer reported: {query}",
                 "tracking_id": tracking_id or ""},
                db, user, provider="offline")
            if outcome.get("ok"):
                result = outcome["result"]
                result["message"] = (
                    f"I've opened support case {result['case_id']} for you. "
                    "A FedEx representative will follow up shortly."
                )
                return {"intent": "escalate", "data": result}
        return {"intent": "escalate",
                "data": {"message": "Please log in so I can open a support case for you, "
                                    "or call FedEx customer care."}}

    guidance = {
        "reschedule": "To reschedule, use POST /reschedule with your tracking_id and new_date.",
        "redirect": "To redirect, use POST /redirect with your tracking_id and new_address.",
        "cancel": "To cancel, use POST /cancel with your tracking_id.",
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
        for provider in _provider_chain():
            try:
                history = conversation_store.get_history(session_id)
                result = _run_agentic(provider, query, db, user, history)
                # Persist only the final user/assistant text — not tool scaffolding.
                conversation_store.append_turn(session_id, query, result.get("response", ""))
                return result
            except ProviderBillingError as exc:
                # Out of credits / bad key: demote for this process and try
                # the next brain instead of dropping to the keyword router.
                logger.warning(f"Provider '{provider.name}' unavailable ({exc}); trying next provider.")
                llm_service.demote(provider.name)
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
