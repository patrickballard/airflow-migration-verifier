from openai import OpenAI

def test_models(httpx_mock):
    httpx_mock.add_response(url="https://api.example.invalid/v1/models",
                            json={"object": "list", "data": [{"id": "mocked", "object": "model", "created": 0, "owned_by": "x"}]})
    c = OpenAI(api_key="x", base_url="https://api.example.invalid/v1", max_retries=0)
    assert [m.id for m in c.models.list()] == ["mocked"]
