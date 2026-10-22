import httpx
from openai import OpenAI
from mock import start
c = OpenAI(api_key="x", base_url=start(), timeout=httpx.Timeout(10.0, connect=2.0))
print("models:", [m.id for m in c.models.list()])
