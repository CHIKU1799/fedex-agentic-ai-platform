"""A scripted stand-in for the Anthropic client, so the agentic tool-calling
loop can be exercised deterministically without network or API keys."""
import json
import types


def msg(content=None, tool_calls=None, usage=(100, 20)):
    blocks = []
    if content:
        blocks.append(types.SimpleNamespace(type="text", text=content))
    if tool_calls:
        blocks.extend(tool_calls)
    input_tokens, output_tokens = usage
    usage_obj = types.SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    stop_reason = "tool_use" if tool_calls else "end_turn"
    return types.SimpleNamespace(content=blocks, stop_reason=stop_reason, usage=usage_obj)


def tool_call(call_id, name, arguments_json):
    return types.SimpleNamespace(
        type="tool_use", id=call_id, name=name, input=json.loads(arguments_json or "{}")
    )


class FakeAnthropic:
    """Replays a scripted list of Messages API responses in order."""

    def __init__(self, scripted_responses):
        self._responses = list(scripted_responses)
        self.calls = []
        outer = self

        class _Messages:
            def create(self, **kwargs):
                outer.calls.append(kwargs)
                return outer._responses.pop(0)

        self.messages = _Messages()


# Backwards-compatible alias for older test imports.
FakeOpenAI = FakeAnthropic
