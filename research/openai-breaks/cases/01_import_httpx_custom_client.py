# App code that builds its own httpx client (proxy, timeouts, etc.)
import httpx
from openai import OpenAI
from mock import start
c = OpenAI(api_key="x", base_url=start(), http_client=httpx.Client(timeout=10))
print("models:", [m.id for m in c.models.list()])
