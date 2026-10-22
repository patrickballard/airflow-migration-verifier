"""Tiny local stand-in for the OpenAI API. start() -> base_url."""
import json, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

CHAT = {"id": "c", "object": "chat.completion", "created": 0, "model": "m",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "{\"a\": 1}"}}]}
RESP = {"id": "r", "object": "response", "created_at": 0, "model": "m", "status": "completed", "output": [],
        "parallel_tool_calls": True, "tool_choice": "auto", "tools": []}
MODELS = {"object": "list", "data": [{"id": "m", "object": "model", "created": 0, "owned_by": "x"}]}
LAST = {}

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, body):
        b = json.dumps(body).encode()
        self.send_response(200); self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        self._send(MODELS)
    def do_POST(self):
        LAST["body"] = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
        LAST["path"] = self.path
        extra = LAST["body"].pop("__reply__", None)
        self._send(extra or (RESP if "/responses" in self.path else CHAT))

def start():
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_port}/v1"
