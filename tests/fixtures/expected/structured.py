"""Structured output helpers.

Docs mention client.beta.chat.completions.parse -- this string must not change.
"""
from openai import AsyncOpenAI, OpenAI
from pydantic import BaseModel


class Weather(BaseModel):
    city: str
    temp_c: float


def extract(client: OpenAI, text: str) -> Weather:
    # keep this comment
    completion = client.chat.completions.parse(  # inline comment survives
        model="gpt-4o-2024-08-06",
        messages=[{"role": "user", "content": text}],
        response_format=Weather,
    )
    return completion.choices[0].message.parsed


def extract_stream(client: OpenAI, text: str) -> Weather:
    with client.chat.completions.stream(
        model="gpt-4o-2024-08-06",
        messages=[{"role": "user", "content": text}],
        response_format=Weather,
    ) as stream:
        final = stream.get_final_completion()
    return final.choices[0].message.parsed


async def extract_async(client: AsyncOpenAI, text: str) -> Weather:
    completion = await (
        client
        .chat
        .completions
        .parse(model="gpt-4o-2024-08-06", messages=[{"role": "user", "content": text}], response_format=Weather)
    )
    return completion.choices[0].message.parsed


def plain(client: OpenAI, text: str) -> str:
    # Not covered by the rule: create() was never beta-only.
    r = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": text}])
    return r.choices[0].message.content
