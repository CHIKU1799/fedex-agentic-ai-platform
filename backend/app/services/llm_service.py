from anthropic import Anthropic

from app.core.config import settings

_client = None


def get_llm_client():
    """Lazily construct a shared Anthropic client, or return None if unconfigured.

    Returning None (rather than raising) is what lets the whole platform
    degrade gracefully to the offline/keyword path when no key is present.
    """
    global _client
    if _client is None:
        api_key = settings.ANTHROPIC_API_KEY
        if not api_key:
            return None
        _client = Anthropic(api_key=api_key)
    return _client


# Backwards-compatible aliases.
get_openai_client = get_llm_client
_get_client = get_llm_client


def generate_llm_response(query: str):
    try:
        client = get_llm_client()
        if client is None:
            return "LLM Error: ANTHROPIC_API_KEY not configured. Set it in .env file."

        response = client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=settings.ANTHROPIC_MAX_TOKENS,
            system="You are a FedEx assistant.",
            messages=[{"role": "user", "content": query}],
        )

        return next((b.text for b in response.content if b.type == "text"), "")

    except Exception as e:
        return f"LLM Error: {str(e)}"
