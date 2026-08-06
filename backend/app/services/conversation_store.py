"""
Lightweight in-memory conversation memory for the agentic planner.

Multi-turn context is what lets the agent resolve "reschedule *it* to Friday"
against the shipment discussed two turns earlier — i.e. behave like an agent in
a conversation rather than a stateless Q&A endpoint.

History is bounded (``MAX_TURNS`` most-recent messages) so token cost per turn
stays flat instead of growing unbounded with the conversation. For a single
demo node an in-process dict is enough; production would back this with Redis
(already provisioned in docker-compose) keyed by session, with a TTL.
"""
from collections import OrderedDict

# Keep the last N messages (user + assistant turns). Tool-call/tool-result
# scaffolding is NOT persisted — only the final user/assistant exchange — so
# replayed history stays small and cheap.
MAX_TURNS = 8
# Cap the number of live sessions to bound memory (LRU eviction).
MAX_SESSIONS = 500

_sessions: "OrderedDict[str, list]" = OrderedDict()


def get_history(session_id: str) -> list:
    if not session_id:
        return []
    history = _sessions.get(session_id, [])
    # Touch for LRU recency.
    if session_id in _sessions:
        _sessions.move_to_end(session_id)
    return list(history)


def append_turn(session_id: str, user_text: str, assistant_text: str) -> None:
    if not session_id:
        return
    history = _sessions.get(session_id, [])
    history.append({"role": "user", "content": user_text})
    if assistant_text:
        history.append({"role": "assistant", "content": assistant_text})
    # Trim to the most recent MAX_TURNS messages.
    _sessions[session_id] = history[-MAX_TURNS:]
    _sessions.move_to_end(session_id)
    # Evict least-recently-used sessions beyond the cap.
    while len(_sessions) > MAX_SESSIONS:
        _sessions.popitem(last=False)


def reset(session_id: str) -> None:
    _sessions.pop(session_id, None)
