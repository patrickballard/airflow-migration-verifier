import httpx
from openai import OpenAI
from mock import start
c = OpenAI(api_key="x", base_url=start())
raw = c.models.with_raw_response.list()
assert isinstance(raw.http_response, httpx.Response), f"http_response is {type(raw.http_response).__module__}.{type(raw.http_response).__name__}"
print("isinstance httpx.Response: True")
