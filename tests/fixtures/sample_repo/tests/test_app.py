import asyncio

from app import structured, weather
from tests.fakes import AsyncFakeCompletions, FakeCompletions, make_client


def test_ask_weather_runs_the_function_the_model_picked():
    client = make_client(FakeCompletions(call_args={"city": "Paris"}))
    assert weather.ask_weather(client, "weather in Paris?") == {"city": "Paris", "temp_c": 21.0}


def test_extract_returns_parsed():
    w = structured.Weather(city="Oslo", temp_c=3.5)
    assert structured.extract(make_client(FakeCompletions(parsed=w)), "x") == w


def test_extract_stream_returns_parsed():
    w = structured.Weather(city="Oslo", temp_c=3.5)
    assert structured.extract_stream(make_client(FakeCompletions(parsed=w)), "x") == w


def test_extract_async_returns_parsed():
    w = structured.Weather(city="Lima", temp_c=19.0)
    client = make_client(AsyncFakeCompletions(parsed=w))
    assert asyncio.run(structured.extract_async(client, "x")) == w


def test_plain():
    assert structured.plain(make_client(FakeCompletions()), "x") == "hi"
