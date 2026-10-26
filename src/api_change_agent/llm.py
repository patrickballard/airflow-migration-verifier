"""Claude-written patches for ``fix: llm`` rules."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from .codemod import Site
from .rules import Rule

DEFAULT_MODEL = "claude-opus-5-5"

_FILE_BLOCK = re.compile(r"<updated_file>\n?(.*?)</updated_file>", re.S)


class PatchError(RuntimeError):
    pass


@dataclass(frozen=True)
class PatchRequest:
    path: str
    source: str
    rule: Rule
    sites: list[Site]
    feedback: str | None = None


class PatchModel(Protocol):
    def propose(self, request: PatchRequest) -> str:
        """Return the full updated file."""


SYSTEM = """\
You migrate Python code to a new major version of a library. You get one file, the call \
sites a migration rule flagged, and the rule's migration notes. Return the \
complete updated file through the submit_patch tool.

- Change only what the migration needs: the flagged calls and the code that \
reads their results or builds their follow-up messages.
- Preserve every existing comment exactly as written, including comments on \
lines you change (keep them on the rewritten line).
- Keep everything else byte-for-byte: comments, blank lines, quoting style, \
import order, unrelated code.
- The file must stay valid Python and keep its public behaviour; the repo's \
tests will run against it.
- If a site can't be migrated safely in this file alone, still return the \
file, leave that site as it is, and say why in `notes`.
- Always answer by calling submit_patch exactly once. If you cannot use the \
tool, reply with the whole file inside <updated_file></updated_file> tags."""

SUBMIT_TOOL = {
    "name": "submit_patch",
    "description": "Submit the complete migrated file.",
    "input_schema": {
        "type": "object",
        "properties": {
            "updated_source": {
                "type": "string",
                "description": "The entire file after the migration, exactly as it should be written to disk.",
            },
            "notes": {
                "type": "string",
                "description": "Anything a reviewer should check, or sites left unmigrated and why.",
            },
        },
        "required": ["updated_source"],
    },
}


def build_prompt(req: PatchRequest) -> str:
    def site(s):
        line = f"- line {s.line}: `{' '.join(s.snippet.split())[:300]}`"
        return f"{line}: {s.reason}" if s.reason else line

    parts = [
        f"File: `{req.path}`",
        f"Rule `{req.rule.id}`: {req.rule.description}",
        f"Minimum library version for the new form: {req.rule.min_sdk_version}. Reference: {req.rule.changelog}",
        f"Migration notes:\n{req.rule.guidance.strip()}",
        f"Flagged sites (and why each one needs judgment):\n" + "\n".join(site(s) for s in req.sites),
        f"Current file:\n<file>\n{req.source}</file>",
    ]
    if req.feedback:
        parts.append(f"Your previous patch for this file was rejected:\n<feedback>\n{req.feedback}\n</feedback>\n"
                     "Start again from the current file above.")
    return "\n\n".join(parts)


# USD per million tokens: (input, output, cache read, cache write). Anthropic first-party
# rates; a model not listed is priced at the most expensive row so a budget errs safe.
PRICES = {
    "claude-fable-5-1": (10.00, 50.00, 0.25, 12.50),
    "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00),
    "claude-opus-5": (5.00, 25.00, 0.50, 6.25),
    "claude-opus-4-8": (5.00, 25.00, 0.50, 6.25),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20, 2.50),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
}
_WORST = max(PRICES.values(), key=lambda p: p[1])
# Models that take the server-side `fallbacks: "default"` refusal fallback on the Claude API.
FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MIN_USEFUL_OUTPUT = 4000  # don't start a call that can't afford a whole file back


class BudgetExhausted(PatchError):
    pass


@dataclass
class Usage:
    model: str
    input_tokens: int
    output_tokens: int
    cache_read: int
    cache_write: int
    usd: float
    stop_reason: str


def cost(model: str, input_tokens: int, output_tokens: int, cache_read: int = 0, cache_write: int = 0) -> float:
    i, o, cr, cw = PRICES.get(model, _WORST)
    return (input_tokens * i + output_tokens * o + cache_read * cr + cache_write * cw) / 1_000_000


class ClaudePatchModel:
    """Claude via the Anthropic SDK, with a hard USD budget across all calls."""

    def __init__(self, client=None, model: str = DEFAULT_MODEL, max_tokens: int = 32000,
                 max_usd: float | None = None):
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self.client = client
        self.model = model
        self.max_tokens = max_tokens
        self.max_usd = max_usd
        self.calls: list[Usage] = []
        self.last_notes: str = ""

    @property
    def spent_usd(self) -> float:
        return sum(u.usd for u in self.calls)

    def _output_allowance(self, prompt: str) -> int:
        """max_tokens for the next call so that even a full-length reply stays within budget."""
        if self.max_usd is None:
            return self.max_tokens
        i, o, *_ = PRICES.get(self.model, _WORST)
        est_input = (len(prompt) + len(SYSTEM) + 2000) / 3  # generous chars->tokens, + tool schema
        left = self.max_usd - self.spent_usd - est_input * i / 1_000_000
        allowance = int(left * 1_000_000 / o)
        if allowance < MIN_USEFUL_OUTPUT:
            raise BudgetExhausted(f"LLM budget ${self.max_usd:.2f} used up (spent ${self.spent_usd:.4f})")
        return min(self.max_tokens, allowance)

    def propose(self, request: PatchRequest) -> str:
        self.last_notes = ""
        prompt = build_prompt(request)
        max_tokens = self._output_allowance(prompt)
        kwargs = dict(model=self.model, max_tokens=max_tokens, system=SYSTEM, tools=[SUBMIT_TOOL],
                      # "auto": current models reject forced tool use; the system prompt asks for the call
                      tool_choice={"type": "auto"},
                      messages=[{"role": "user", "content": prompt}])
        # Streamed: a whole file back can exceed the non-streaming time limit.
        if self.model in FALLBACK_MODELS:
            stream_ctx = self.client.beta.messages.stream(betas=[FALLBACK_BETA], fallbacks="default", **kwargs)
        else:
            stream_ctx = self.client.messages.stream(**kwargs)
        with stream_ctx as stream:
            message = stream.get_final_message()
        self._record(message)
        if message.stop_reason == "refusal":
            details = getattr(message, "stop_details", None)
            raise PatchError(f"Claude declined (refusal, category {getattr(details, 'category', None)!r})")
        if message.stop_reason == "max_tokens":
            raise PatchError(f"response hit max_tokens ({max_tokens}); file too large or budget too small")
        for block in message.content:
            if block.type == "tool_use" and block.name == "submit_patch":
                source = block.input.get("updated_source")
                if not isinstance(source, str):
                    raise PatchError("submit_patch had no updated_source")
                self.last_notes = block.input.get("notes", "") or ""
                return source
        text = "".join(block.text for block in message.content if block.type == "text")
        match = _FILE_BLOCK.search(text)
        if match:
            return match.group(1)
        raise PatchError("model did not call submit_patch or return an <updated_file> block")

    def _record(self, message) -> None:
        u = message.usage
        n = lambda v: int(v or 0)
        served = getattr(message, "model", None) or self.model
        usage = Usage(served, n(u.input_tokens), n(u.output_tokens), n(getattr(u, "cache_read_input_tokens", 0)),
                      n(getattr(u, "cache_creation_input_tokens", 0)), 0.0, str(message.stop_reason))
        usage.usd = cost(served, usage.input_tokens, usage.output_tokens, usage.cache_read, usage.cache_write)
        self.calls.append(usage)

    def usage_summary(self) -> dict:
        return {"calls": len(self.calls), "usd": round(self.spent_usd, 4), "budget_usd": self.max_usd,
                "input_tokens": sum(u.input_tokens for u in self.calls),
                "output_tokens": sum(u.output_tokens for u in self.calls),
                "models": sorted({u.model for u in self.calls})}
