# 2.0.0: function_call_output.output may be a list; code assumes str
from openai import OpenAI
from mock import start, RESP
c = OpenAI(api_key="x", base_url=start())
reply = dict(RESP, output=[{"type": "function_call_output", "id": "o", "call_id": "c", "status": "completed",
                            "output": [{"type": "input_text", "text": "hi"}]}])
r = c.responses.create(model="m", input="x", extra_body={"__reply__": reply})
out = r.output[0].output
print("output type:", type(out).__name__, "| .upper():", out.upper() if isinstance(out, str) else "n/a (list)")
