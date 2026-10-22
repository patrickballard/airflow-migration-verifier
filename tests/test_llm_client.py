"""ClaudePatchModel through the real anthropic SDK, against a local SSE server."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import anthropic
import pytest

from api_change_agent.codemod import find_sites
from api_change_agent.llm import ClaudePatchModel, PatchError, PatchRequest
from api_change_agent.rules import load_rules
from conftest import FIXTURES, RULES, SAMPLE_REPO

RULE = load_rules(RULES).rules[1]


def sse(stop_reason, tool_input):
    events = [
        ("message_start", {"type": "message_start", "message": {
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5", "content": [],
            "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 1}}}),
    ]
    if tool_input is not None:
        events += [
            ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {
                "type": "tool_use", "id": "toolu_1", "name": "submit_patch", "input": {}}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {
                "type": "input_json_delta", "partial_json": json.dumps(tool_input)}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ]
    events += [
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                           "usage": {"output_tokens": 50}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    return "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in events).encode()


@pytest.fixture
def server():
    state = {"requests": [], "reply": None}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            state["requests"].append(json.loads(self.rfile.read(int(self.headers["content-length"]))))
            body = state["reply"]
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            self.wfile.write(body)

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    state["url"] = f"http://127.0.0.1:{srv.server_port}"
    yield state
    srv.shutdown()


def model(server):
    client = anthropic.Anthropic(api_key="test-key", base_url=server["url"], max_retries=0)
    return ClaudePatchModel(client=client)


def request(feedback=None):
    src = (SAMPLE_REPO / "app" / "weather.py").read_text()
    return PatchRequest("app/weather.py", src, RULE, find_sites(src, RULE), feedback)


def test_returns_tool_source_and_sends_forced_tool(server):
    good = (FIXTURES / "llm" / "weather_good.py").read_text()
    server["reply"] = sse("tool_use", {"updated_source": good, "notes": "ok"})
    m = model(server)
    assert m.propose(request(feedback="tests failed: tests.test_app::x")) == good
    assert m.last_notes == "ok"
    (req,) = server["requests"]
    assert req["model"] == "claude-opus-5-5"
    assert req["stream"] is True
    assert req["tool_choice"] == {"type": "tool", "name": "submit_patch"}
    assert req["tools"][0]["name"] == "submit_patch"
    prompt = req["messages"][0]["content"]
    assert "line 22" in prompt and "functions=[WEATHER_FN]" in prompt
    assert "tool_choice" in prompt  # rule guidance included
    assert "<feedback>\ntests failed: tests.test_app::x" in prompt


def test_truncated_response_is_an_error(server):
    server["reply"] = sse("max_tokens", {"updated_source": "x"})
    with pytest.raises(PatchError, match="max_tokens"):
        model(server).propose(request())


def test_no_tool_call_is_an_error(server):
    server["reply"] = sse("end_turn", None)
    with pytest.raises(PatchError, match="submit_patch"):
        model(server).propose(request())
