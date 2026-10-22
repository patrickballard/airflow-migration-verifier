"""A tiny stand-in for the OpenAI client, shaped like the real SDK.

Like the SDK, ``client.beta.chat`` is the same object as ``client.chat``.
Function-calling responses follow whichever API the caller used.
"""
import json
from types import SimpleNamespace as NS


def _tool_message(name, arguments):
    fn = NS(name=name, arguments=json.dumps(arguments))
    return NS(role="assistant", content=None, function_call=None,
              tool_calls=[NS(id="call_1", type="function", function=fn)])


def _function_message(name, arguments):
    fn = NS(name=name, arguments=json.dumps(arguments))
    return NS(role="assistant", content=None, function_call=fn, tool_calls=None)


class _Stream:
    def __init__(self, completion):
        self._completion = completion

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_completion(self):
        return self._completion


class FakeCompletions:
    def __init__(self, parsed=None, call_args=None):
        self.calls = []
        self._parsed = parsed
        self._call_args = call_args or {"city": "Paris"}

    def _choice(self, message):
        return NS(choices=[NS(index=0, finish_reason="stop", message=message)])

    def create(self, **kwargs):
        self.calls.append(("create", kwargs))
        if "tools" in kwargs:
            name = kwargs["tools"][0]["function"]["name"]
            return self._choice(_tool_message(name, self._call_args))
        if "functions" in kwargs:
            name = kwargs["functions"][0]["name"]
            return self._choice(_function_message(name, self._call_args))
        return self._choice(NS(role="assistant", content="hi", function_call=None, tool_calls=None))

    def parse(self, **kwargs):
        self.calls.append(("parse", kwargs))
        return self._choice(NS(role="assistant", content=None, parsed=self._parsed))

    def stream(self, **kwargs):
        self.calls.append(("stream", kwargs))
        return _Stream(self._choice(NS(role="assistant", content=None, parsed=self._parsed)))


class AsyncFakeCompletions(FakeCompletions):
    async def parse(self, **kwargs):
        return FakeCompletions.parse(self, **kwargs)


def make_client(completions):
    chat = NS(completions=completions)
    return NS(chat=chat, beta=NS(chat=chat))
