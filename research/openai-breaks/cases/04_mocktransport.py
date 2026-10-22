import httpx
from openai import OpenAI
def handler(request):
    return httpx.Response(200, json={"object": "list", "data": [{"id": "mt", "object": "model", "created": 0, "owned_by": "x"}]})
c = OpenAI(api_key="x", base_url="https://api.example.invalid/v1", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
print("models:", [m.id for m in c.models.list()])
