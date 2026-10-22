# Typical test: respx intercepts the SDK's HTTP calls (no server at this host)
import respx, httpx
from openai import OpenAI
with respx.mock(assert_all_called=False) as router:
    router.get("https://api.example.invalid/v1/models").mock(
        return_value=httpx.Response(200, json={"object": "list", "data": [{"id": "mocked", "object": "model", "created": 0, "owned_by": "x"}]}))
    c = OpenAI(api_key="x", base_url="https://api.example.invalid/v1", max_retries=0)
    print("models:", [m.id for m in c.models.list()])
