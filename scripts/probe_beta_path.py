import json, threading, warnings, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
import openai
from pydantic import BaseModel

BODY = {"id":"x","object":"chat.completion","created":0,"model":"m",
        "choices":[{"index":0,"finish_reason":"stop","message":{"role":"assistant","content":"{\"a\":1}"}}]}
SSE = ('data: {"id":"x","object":"chat.completion.chunk","created":0,"model":"m","choices":[{"index":0,"delta":{"role":"assistant","content":"{\\"a\\":1}"},"finish_reason":null}]}\n\n'
       'data: {"id":"x","object":"chat.completion.chunk","created":0,"model":"m","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
       'data: [DONE]\n\n')
class H(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["content-length"])))
        if req.get("stream"):
            self.send_response(200); self.send_header("content-type","text/event-stream"); self.end_headers(); self.wfile.write(SSE.encode())
        else:
            b=json.dumps(BODY).encode(); self.send_response(200); self.send_header("content-type","application/json"); self.send_header("content-length",str(len(b))); self.end_headers(); self.wfile.write(b)
srv = HTTPServer(("127.0.0.1",0),H); threading.Thread(target=srv.serve_forever,daemon=True).start()
c = openai.OpenAI(api_key="sk-test", base_url=f"http://127.0.0.1:{srv.server_port}/v1", max_retries=0)
class A(BaseModel): a:int
msgs=[{"role":"user","content":"hi"}]
for label, ns in [("beta", c.beta.chat.completions), ("ga", c.chat.completions)]:
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        r = ns.parse(model="m", messages=msgs, response_format=A)
        print(f"{label}.parse  -> {r.choices[0].message.parsed!r}  warnings={[str(x.message) for x in w]}")
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        with ns.stream(model="m", messages=msgs, response_format=A) as s:
            final = s.get_final_completion()
        print(f"{label}.stream -> {final.choices[0].message.parsed!r}  warnings={[str(x.message) for x in w]}")
# async too
import asyncio
async def main():
    ac = openai.AsyncOpenAI(api_key="sk-test", base_url=f"http://127.0.0.1:{srv.server_port}/v1", max_retries=0)
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        r = await ac.beta.chat.completions.parse(model="m", messages=msgs, response_format=A)
    print(f"async beta.parse -> {r.choices[0].message.parsed!r} warnings={[str(x.message) for x in w]}")
asyncio.run(main())
