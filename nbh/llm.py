"""A thin seam onto the model. Deliberately thin.

There is no framework here and there will not be one. The whole argument of
this project is that an agent is a model choosing an action in a loop, and
that every layer you put between you and that choice is a place where a
decision happens that you can no longer read. See `recherche-orbio.md` §3.1.

So this module does exactly three things: send a request, force the answer
into a shape we defined, and record what it cost.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Any

#: The agent that plays the exchange. Capable enough to hold a role under
#: pressure across many turns.
AGENT_MODEL = "claude-sonnet-5"

#: The assessor and the judge. Reading a transcript and checking it against a
#: rule is an easier task than producing the transcript, so this stays small
#: and cheap on purpose -- supervision cost should scale with volume, not
#: with the capability of the thing being supervised.
JUDGE_MODEL = "claude-haiku-4-5-20251001"


class LLMError(RuntimeError):
    """The model did not return something we can use."""


@dataclass
class Usage:
    """What the runs cost. Tracked because cost predictability is a
    reliability property, not an afterthought."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    by_model: dict[str, int] = field(default_factory=dict)

    def add(self, model: str, resp: Any) -> None:
        self.calls += 1
        self.input_tokens += getattr(resp.usage, "input_tokens", 0)
        self.output_tokens += getattr(resp.usage, "output_tokens", 0)
        self.by_model[model] = self.by_model.get(model, 0) + 1

    def add_counts(self, model: str, *, input_tokens: int, output_tokens: int,
                   cost_usd: float = 0.0) -> None:
        """Record a call from a backend that reports plain numbers.

        The CLI hands back counts rather than a response object, and its
        `total_cost_usd` is what the same work would have cost on the API --
        list price, not a bill. It is kept because an unpriced run is one
        nobody can size before starting it.
        """
        self.calls += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.cost_usd += cost_usd
        self.by_model[model] = self.by_model.get(model, 0) + 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": round(self.cost_usd, 4),
            "by_model": dict(self.by_model),
        }


class Client:
    """Anthropic access, with structured output and a usage tape."""

    def __init__(self, api_key: str | None = None, *, temperature: float = 1.0) -> None:
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise LLMError(
                "No API key. Set ANTHROPIC_API_KEY, or pass one in. "
                "This project never ships with a key in it."
            )
        # Imported here, not at the top: only this backend needs the SDK, and
        # the desk installs without it (requirements-model.txt).
        try:
            import anthropic
        except ImportError as e:
            raise LLMError("The API backend needs the anthropic package: "
                           "pip install -r requirements-model.txt") from e
        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=key)
        self.temperature = temperature
        self.usage = Usage()

    def structured(
        self,
        *,
        model: str,
        system: str,
        user: str,
        schema: dict[str, Any],
        tool_name: str,
        tool_description: str,
        max_tokens: int = 1200,
    ) -> dict[str, Any]:
        """Get one answer, shaped.

        A single forced tool call rather than "reply in JSON please". The
        difference matters: a schema the API enforces is a contract, a
        schema in a prompt is a hope.

        Note the message array holds exactly one user turn. Conversation
        history does not live here -- it is rendered into `user` as readable
        text by the caller. That is a measured choice, not a stylistic one;
        see `nbh/protocol.py::Message.render`.
        """
        try:
            resp = self._client.messages.create(
                model=model,
                max_tokens=max_tokens,
                temperature=self.temperature,
                system=system,
                tools=[
                    {
                        "name": tool_name,
                        "description": tool_description,
                        "input_schema": schema,
                    }
                ],
                tool_choice={"type": "tool", "name": tool_name},
                messages=[{"role": "user", "content": user}],
            )
        except self._anthropic.APIError as e:
            raise LLMError(f"{model}: {e}") from e

        self.usage.add(model, resp)

        for block in resp.content:
            if block.type == "tool_use" and block.name == tool_name:
                return dict(block.input)
        raise LLMError(f"{model} returned no {tool_name} call: {resp.content!r}")


class ClaudeCodeClient:
    """Model access through the Claude Code CLI, headless. No API key.

    Why this exists: `Client` above needs a paid API key. This one runs on a
    Claude Code subscription instead, so the project can be built, run and
    reproduced by someone who has one and no API budget -- which includes its
    author. Same `structured()` signature, so nothing above this line knows
    which one it is talking to.

    The trade is explicit, and it is the more interesting half of this file.
    The API enforces the shape: `tool_choice` makes the schema a contract the
    server keeps. The CLI enforces nothing at all. It returns whatever text
    the model produced -- and on the first trial run of this client it
    returned JSON wrapped in a markdown fence, and an act the protocol
    forbids.

    So the contract has to live here, in code: extract, parse, check against
    the schema, and raise on anything that does not conform. `nbh/agent.py`
    then replays the turn with the violation quoted back at the model.

    That is the argument of this project in one class. A model is not a
    reliable narrator of its own compliance. What makes an agent dependable
    is the code around it that refuses bad output.

    One honest cost note. Each call carries roughly 17k tokens of Claude Code
    preamble that no flag removes without also demanding an API key, so this
    backend is free in money and not free in quota. Anything that does not
    need a fresh answer should run through `ReplayClient`.
    """

    #: Tools are denied rather than ignored: an agent that can read the repo
    #: it is being evaluated in is not being evaluated.
    _DENIED_TOOLS = (
        "Bash", "Read", "Write", "Edit", "Glob", "Grep", "WebSearch",
        "WebFetch", "Task", "Agent", "NotebookEdit", "TodoWrite", "Skill",
    )

    def __init__(self, *, temperature: float = 1.0, binary: str = "claude",
                 timeout: int = 180, max_attempts: int = 3) -> None:
        if shutil.which(binary) is None:
            raise LLMError(
                f"`{binary}` is not on PATH. Install Claude Code, or use the "
                f"API client, or replay a recorded run."
            )
        self.binary = binary
        self.timeout = timeout
        self.max_attempts = max_attempts
        #: Accepted and ignored. The CLI exposes no temperature control; the
        #: field exists so callers do not have to special-case this backend.
        self.temperature = temperature
        self.usage = Usage()
        #: An empty directory, so the CLI finds no CLAUDE.md, no project and
        #: no files to wander into.
        self._cwd = tempfile.mkdtemp(prefix="nbh-cli-")

    def structured(
        self,
        *,
        model: str,
        system: str,
        user: str,
        schema: dict[str, Any],
        tool_name: str,
        tool_description: str,
        max_tokens: int = 1200,
    ) -> dict[str, Any]:
        """Get one answer, shaped -- by us, because nobody else will."""
        contract = (
            f"{system}\n\n"
            f"# Output contract\n"
            f"{tool_description}\n\n"
            f"Reply with one JSON object and nothing else. No prose before or "
            f"after it, no markdown fence. It must validate against this "
            f"JSON Schema:\n{json.dumps(schema, indent=2)}"
        )

        complaint: str | None = None
        for attempt in range(1, self.max_attempts + 1):
            prompt = user if complaint is None else (
                f"{user}\n\n# Your previous answer was rejected\n{complaint}\n"
                f"Answer again, as one JSON object only."
            )
            text = self._invoke(model, contract, prompt)
            try:
                data = _extract_json(text)
                _check_against_schema(data, schema)
                return data
            except LLMError as e:
                complaint = str(e)
                if attempt == self.max_attempts:
                    raise LLMError(
                        f"{model}: no schema-valid answer in {self.max_attempts} "
                        f"attempts. Last complaint: {complaint}"
                    ) from e
        raise AssertionError("unreachable")

    def _invoke(self, model: str, system: str, prompt: str) -> str:
        cmd = [
            self.binary, "-p", prompt,
            "--output-format", "json",
            "--model", model,
            "--system-prompt", system,
            "--strict-mcp-config",
            "--disable-slash-commands",
            "--no-session-persistence",
            "--disallowed-tools", *self._DENIED_TOOLS,
        ]
        try:
            proc = subprocess.run(
                cmd, cwd=self._cwd, capture_output=True, text=True,
                timeout=self.timeout,
                #: The prompt goes in through -p, so the CLI has nothing to
                #: read. Left inherited, an open parent stdin -- a background
                #: job, a pipe -- makes it wait three seconds and then exit 1.
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired as e:
            raise LLMError(f"{model}: CLI timed out after {self.timeout}s") from e
        if proc.returncode != 0:
            raise LLMError(
                f"{model}: CLI exited {proc.returncode}: "
                f"{(proc.stderr or proc.stdout or '').strip()[:400]}"
            )
        try:
            envelope = json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            raise LLMError(f"{model}: CLI stdout was not JSON: {proc.stdout[:400]}") from e
        if envelope.get("is_error"):
            raise LLMError(f"{model}: CLI reported an error: {envelope.get('result')!r}")

        u = envelope.get("usage") or {}
        self.usage.add_counts(
            model,
            input_tokens=(u.get("input_tokens", 0)
                          + u.get("cache_creation_input_tokens", 0)
                          + u.get("cache_read_input_tokens", 0)),
            output_tokens=u.get("output_tokens", 0),
            cost_usd=envelope.get("total_cost_usd") or 0.0,
        )
        result = envelope.get("result")
        if not isinstance(result, str):
            raise LLMError(f"{model}: CLI returned no text result: {envelope!r}")
        return result


# --------------------------------------------------------------------------
# Making unstructured text keep a contract
# --------------------------------------------------------------------------

def _extract_json(text: str) -> dict[str, Any]:
    """Pull one JSON object out of whatever the model actually sent.

    Three things get past a clear instruction not to do them, in descending
    order of frequency: a markdown fence, a sentence of preamble, and a
    trailing sign-off. Scanning for the first balanced brace handles all
    three without guessing.
    """
    if not isinstance(text, str) or not text.strip():
        raise LLMError("empty answer")
    start = text.find("{")
    if start == -1:
        raise LLMError(f"no JSON object in the answer: {text[:200]!r}")
    depth, in_string, escaped = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                blob = text[start:i + 1]
                try:
                    data = json.loads(blob)
                except json.JSONDecodeError as e:
                    raise LLMError(f"malformed JSON: {e}") from e
                if not isinstance(data, dict):
                    raise LLMError("top level of the answer was not an object")
                return data
    raise LLMError(f"unbalanced JSON object in the answer: {text[:200]!r}")


def _check_against_schema(data: dict[str, Any], schema: dict[str, Any]) -> None:
    """Enforce the part of the schema that carries meaning here.

    Not a general JSON Schema validator, and does not pretend to be: this
    checks required keys, declared types and enums, which is exactly the set
    of constraints the API's `tool_choice` would have kept for us. Anything
    it rejects gets quoted back to the model on the retry, so the complaint
    has to be precise enough to act on.
    """
    for key in schema.get("required", []):
        if data.get(key) in (None, ""):
            raise LLMError(f"`{key}` is required and was missing or empty")

    types = {"string": str, "integer": int, "number": (int, float),
             "boolean": bool, "array": list, "object": dict}
    for key, spec in (schema.get("properties") or {}).items():
        if key not in data:
            continue
        value = data[key]
        declared = spec.get("type")
        allowed = [declared] if isinstance(declared, str) else list(declared or [])
        if allowed:
            if value is None:
                if "null" not in allowed:
                    raise LLMError(f"`{key}` was null; allowed types are {allowed}")
            else:
                py = tuple(types[t] for t in allowed if t in types)
                if py and not isinstance(value, py):
                    raise LLMError(
                        f"`{key}` was {type(value).__name__}, expected {allowed}"
                    )
        if "enum" in spec and value not in spec["enum"]:
            legal = [v for v in spec["enum"] if v is not None]
            raise LLMError(
                f"`{key}` was {value!r}, which is not allowed here. "
                f"Allowed: {', '.join(map(str, legal))}"
                + (" (or null)" if None in spec["enum"] else "")
            )


def default_client(**kw: Any) -> "Client | ClaudeCodeClient":
    """The backend a deployment chose, from `NBH_BACKEND`.

    `claude-code` (the default) runs on the Claude Code CLI and a
    subscription, with no key -- how this repository was built. `api` uses
    `ANTHROPIC_API_KEY`, which is what a team running this for real will want:
    billed per call, no CLI preamble, and a data-processing agreement that
    covers candidates' documents.
    """
    backend = os.environ.get("NBH_BACKEND", "claude-code").strip().lower()
    if backend == "api":
        return Client(**kw)
    if backend in ("claude-code", "cli", ""):
        return ClaudeCodeClient(**kw)
    raise LLMError(f"NBH_BACKEND={backend!r}: use 'claude-code' or 'api'")


class ReplayClient:
    """A stand-in that replays recorded answers instead of calling out.

    Lets the protocol, the harness and the console be exercised end to end
    with no key and no network -- which is also what makes the published
    demo openable by someone who has neither.
    """

    def __init__(
        self,
        answers: list[dict[str, Any]],
        by_tool: dict[str, list[dict[str, Any]]] | None = None,
    ) -> None:
        self._answers = list(answers)
        self._i = 0
        self._by_tool = {k: list(v) for k, v in (by_tool or {}).items()}
        self.usage = Usage()

    def structured(self, **kwargs: Any) -> dict[str, Any]:
        tool = kwargs.get("tool_name")
        if tool in self._by_tool:
            queue = self._by_tool[tool]
            if not queue:
                raise LLMError(f"replay has no more recorded {tool} answers; the run asked for more")
            return queue.pop(0)
        if self._i >= len(self._answers):
            raise LLMError(
                f"replay exhausted after {self._i} answers; the run asked for more"
            )
        answer = self._answers[self._i]
        self._i += 1
        return answer

    @classmethod
    def from_run(cls, path: str) -> "ReplayClient":
        """Replay a trace written by `run.py`, with no model behind it.

        A trace records one decision per committed act, and *not* the
        assessor's or the note writer's answers (`decisions` excludes them
        on purpose, so a turn and a decision stay one to one). Replaying the
        decisions alone therefore handed an act to the assessor and crashed
        on the first disclosure. The other two are rebuilt from what the
        trace does keep:

        - `record_evidence`: the strength the ledger kept for that turn. A
          disclosure the ledger did not keep lost to a stronger (or earlier,
          equal) one, so its exact strength was never stored; it is replayed
          as `none`, which leaves the same ledger -- strongest wins, and any
          disclosure resolves its criterion -- and says nothing it cannot know.
        - `write_note`: the two recorded notes, company then candidate.
        """
        with open(path, encoding="utf-8") as fh:
            run = json.load(fh)
        kept = run.get("exchange", {}).get("ledger", {}).get("evidence", {})
        strengths = []
        for m in run.get("exchange", {}).get("transcript", []):
            if m.get("act") != "disclose":
                continue
            ev = kept.get(m.get("criterion_id"))
            same = ev is not None and ev.get("turn") == m.get("turn")
            strengths.append({
                "reasoning": "replayed: " + ("the strength the ledger kept" if same
                                             else "not kept by the ledger, so not stored"),
                "strength": ev["strength"] if same else "none",
            })
        verdicts = run.get("verdicts", {})
        notes = [{"note": verdicts[side]["note"]} for side in ("company", "candidate")
                 if "note" in verdicts.get(side, {})]
        return cls(run["decisions"], by_tool={"record_evidence": strengths,
                                              "write_note": notes})
