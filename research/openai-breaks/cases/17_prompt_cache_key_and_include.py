from openai import OpenAI
from mock import start, LAST
c = OpenAI(api_key="x", base_url=start())
c.responses.create(model="m", input="x", prompt_cache_key="k", include=["web_search_call.results"])
print("sent:", sorted(k for k in LAST["body"] if k in ("prompt_cache_key", "include")))
