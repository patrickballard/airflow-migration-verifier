import json

import openai

WEATHER_FN = {
    "name": "get_weather",
    "description": "Get the current weather for a city.",
    "parameters": {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    },
}


def lookup(city: str) -> dict:
    return {"city": city, "temp_c": 21.0}


def ask_weather(client: openai.OpenAI, question: str) -> dict:
    """Let the model call get_weather, then run it locally."""
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": question}],
        functions=[WEATHER_FN],  # legacy function calling
        function_call={"name": "get_weather"},
    )
    message = response.choices[0].message
    args = json.loads(message.function_call.arguments)
    return lookup(**args)
