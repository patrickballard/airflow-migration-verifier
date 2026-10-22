# The realistic path: a ComputerTool coming back inside a Response
from openai import OpenAI
from mock import start, RESP
c = OpenAI(api_key="x", base_url=start())
reply = dict(RESP, tools=[{"type": "computer_use_preview", "display_width": 1024, "display_height": 768, "environment": "browser"}])
r = c.responses.create(model="m", input="x", extra_body={"__reply__": reply})
print("tools[0].display_width:", r.tools[0].display_width, type(r.tools[0]).__name__)
