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
You migrate Python code that uses the openai SDK. You get one file, the call \
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
    sites = "\n".join(f"- line {s.line}: `{' '.join(s.snippet.split())[:300]}`" for s in req.sites)
    parts = [
        f"File: `{req.path}`",
        f"Rule `{req.rule.id}`: {req.rule.description}",
        f"Minimum openai version for the new form: {req.rule.min_sdk_version}. Changelog: {req.rule.changelog}",
        f"Migration notes:\n{req.rule.guidance.strip()}",
        f"Flagged call sites:\n{sites}",
        f"Current file:\n<file>\n{req.source}</file>",
    ]
    if req.feedback:
        parts.append(f"Your previous patch for this file was rejected:\n<feedback>\n{req.feedback}\n</feedback>\n"
                     "Start again from the current file above.")
    return "\n\n".join(parts)


class ClaudePatchModel:
    def __init__(self, client=None, model: str = DEFAULT_MODEL, max_tokens: int = 32000):
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self.client = client
        self.model = model
        self.max_tokens = max_tokens
        self.last_notes: str = ""

    def propose(self, request: PatchRequest) -> str:
        # Streamed: a whole file back can exceed the non-streaming time limit.
        # tool_choice stays "auto": some models reject forced tool use
        # ("tool"/"any"), so the system prompt asks for the call instead.
        with self.client.messages.stream(
            model=self.model,
            max_tokens=self.max_tokens,
            system=SYSTEM,
            tools=[SUBMIT_TOOL],
            tool_choice={"type": "auto"},
            messages=[{"role": "user", "content": build_prompt(request)}],
        ) as stream:
            message = stream.get_final_message()
        if message.stop_reason == "max_tokens":
            raise PatchError(f"response hit max_tokens ({self.max_tokens}); file too large")
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
            self.last_notes = ""
            return match.group(1)
        raise PatchError("model did not call submit_patch or return an <updated_file> block")
