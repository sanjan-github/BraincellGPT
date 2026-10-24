import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from backend.agent import _read_anthropic_stream, _read_openai_stream, run_agent


class LineResponse:
    def __init__(self, body: bytes):
        self.lines = iter(body.splitlines(keepends=True))

    def readline(self, _limit=-1):
        return next(self.lines, b"")


def frame(data: dict, event: str | None = None) -> bytes:
    prefix = f"event: {event}\n" if event else ""
    return (prefix + "data: " + json.dumps(data) + "\n\n").encode()


class ProviderStreamingParserTests(unittest.TestCase):
    def test_openai_stream_collects_text_and_fragmented_tool_calls(self):
        response = LineResponse(
            frame({"choices": [{"delta": {"content": "Hello "}}]})
            + frame({"choices": [{"delta": {"content": "world"}}]})
            + frame({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call-1", "function": {"name": "read_file", "arguments": '{"path":'}}]}}]})
            + frame({"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"a.py"}'}}]}}]})
            + b"data: [DONE]\n\n"
        )
        events = []
        assistant, calls = _read_openai_stream(response, events.append)

        self.assertEqual(assistant["content"], "Hello world")
        self.assertEqual(calls[0]["function"]["name"], "read_file")
        self.assertEqual(json.loads(calls[0]["function"]["arguments"]), {"path": "a.py"})
        self.assertEqual([event["delta"] for event in events], ["Hello ", "world"])

    def test_anthropic_stream_collects_text_and_tool_json(self):
        response = LineResponse(
            frame({"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": "tool-1", "name": "read_file", "input": {}}}, "content_block_start")
            + frame({"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": '{"path":"a.py"}'}}, "content_block_delta")
            + frame({"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": "Inspecting."}}, "content_block_start")
            + frame({"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "Done."}}, "content_block_delta")
        )
        events = []
        assistant, calls = _read_anthropic_stream(response, events.append)
        self.assertEqual(assistant["content"], "Done.")
        self.assertEqual(json.loads(calls[0]["function"]["arguments"]), {"path": "a.py"})
        self.assertEqual(events[0]["delta"], "Done.")

    def test_stream_parser_rejects_invalid_json(self):
        with self.assertRaisesRegex(ValueError, "malformed streaming JSON"):
            _read_openai_stream(LineResponse(b"data: {broken}\n\n"), lambda _event: None)

    def test_streamed_agent_requests_stream_flag_and_emits_deltas(self):
        response = MagicMock()
        response.readline.side_effect = [b'data: {"choices":[{"delta":{"content":" streamed"}}]}\n', b"\n", b"data: [DONE]\n", b"\n", b""]
        response.__enter__.return_value = response
        with patch("backend.agent.open_provider_request", return_value=response) as opener:
            events = []
            result = run_agent(
                "explain", [], Path.cwd(), "groq", "api-key", "", "", {},
                mode="ask", event_callback=events.append,
            )
        payload = json.loads(opener.call_args.args[0].data.decode())
        self.assertTrue(payload["stream"])
        self.assertEqual(result["reply"], "streamed")
        self.assertEqual(events[0], {"type": "text_delta", "delta": " streamed"})


if __name__ == "__main__":
    unittest.main()
