# Common retry/proxy idiom: SDK helper + an httpx transport
import httpx
from openai import OpenAI, DefaultHttpxClient
from mock import start
c = OpenAI(api_key="x", base_url=start(), http_client=DefaultHttpxClient(transport=httpx.HTTPTransport(retries=3)))
print("models:", [m.id for m in c.models.list()])
