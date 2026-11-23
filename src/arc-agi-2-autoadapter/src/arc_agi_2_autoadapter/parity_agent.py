"""Parity agent: the official ARC Prize harness call, as a Harbor agent.

This agent reproduces what ``arcprize/arc-agi-benchmarking`` does for one
attempt with an OpenAI-compatible provider (``provider: openai``,
``api_type: chat_completions``):

1. Send the task instruction as one user message to the Chat Completions API
   (``OpenAIBaseAdapter._chat_completion`` / ``_chat_completion_stream``).
2. Take the message content, stripped (``_get_content``).
3. Parse it with the harness parser (``utils/parsing.parse_and_validate_json``:
   ``\\boxed{}`` first, then the last bracketed list of lists), then validate it
   as ``List[List[int]]`` (the ``Attempt.answer`` pydantic field).
4. On any error (API error, token-count mismatch, parse failure) retry, up to
   ``retry_attempts`` calls in total (harness default: 2).

The parsed grid is written to ``/app/output.json`` in the task environment,
where the Harbor verifier checks it. The model call runs on the Harbor host,
like the harness runs on the user's machine.

Run with (from the repository root):

    PYTHONPATH=src/arc-agi-2-autoadapter/src harbor run -p datasets/arc-agi-2-autoadapter \\
        -a arc_agi_2_autoadapter.parity_agent:ArcHarnessAgent -m openai/<model> \\
        --ak max_tokens=131072 --ak stream=true

``OPENAI_API_KEY`` and (optionally) ``OPENAI_BASE_URL`` are read from the
environment, as the harness's OpenAI client does.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, List, Optional

from pydantic import Field, TypeAdapter

from harbor.agents.base import BaseAgent
from harbor.agents.options import AgentOptions
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

AGENT_NAME = "arc-harness-chat"
AGENT_VERSION = "1.0.0"
ANSWER_PATH = "/app/output.json"


# --- Parser: copied from arcprize/arc-agi-benchmarking (MIT License,
# --- Copyright (c) 2025 ARC Prize Foundation; see prompts/LICENSE-arc-agi-benchmarking.md),
# --- src/arc_agi_benchmarking/utils/parsing.py at commit 54629ad8.


def backscan_json_parser(log_str: str) -> Optional[List[List[int]]]:
    """
    Extract the last valid JSON substring that matches the List[List] structure
    from the given log string by scanning backwards from the end.
    """
    last_bracket_idx = -1
    closing_bracket = None
    for i in range(len(log_str) - 1, -1, -1):
        char = log_str[i]
        if char in (']', '}'):
            last_bracket_idx = i
            closing_bracket = char
            break

    if last_bracket_idx == -1:
        return None

    opening_bracket = '[' if closing_bracket == ']' else '{'

    bracket_counter = 1  # Start at 1 to account for the found closing bracket
    start_idx = -1

    for i in range(last_bracket_idx - 1, -1, -1):
        char = log_str[i]
        if char == closing_bracket:
            bracket_counter += 1
        elif char == opening_bracket:
            bracket_counter -= 1
            if bracket_counter == 0:
                start_idx = i
                break

    if start_idx == -1:
        return None

    json_candidate = log_str[start_idx:last_bracket_idx + 1]

    try:
        parsed_json = json.loads(json_candidate)

        # Validate the structure: must be a non-empty list of lists.
        if isinstance(parsed_json, list) and parsed_json and all(isinstance(row, list) for row in parsed_json):
            return parsed_json
        else:
            return None

    except json.JSONDecodeError:
        return None


def extract_from_boxed(log_str: str) -> Optional[List[List[int]]]:
    """
    Extracts JSON from a LaTeX-style \\boxed{} command in a string.
    """
    match = re.search(r"\\boxed\{(.*?)\}", log_str, re.DOTALL)
    if match:
        content = match.group(1).strip()
        try:
            # The content inside boxed is often a list of lists
            parsed_json = json.loads(content)
            if isinstance(parsed_json, list) and all(isinstance(i, list) for i in parsed_json):
                return parsed_json
        except json.JSONDecodeError:
            # If json.loads fails, it's not the JSON we're looking for.
            pass
    return None


def parse_and_validate_json(response: str) -> Optional[List[List[int]]]:
    """
    Orchestrates parsing by trying the provider extractor.
    Returns the parsed List[List[int]] or raises ValueError if validation fails.
    """
    parsing_attempts = [
        extract_from_boxed,
        backscan_json_parser
    ]

    for parser in parsing_attempts:
        result = parser(response)
        if result is not None:
            # Validate the structure: must be list of lists
            if isinstance(result, list) and all(isinstance(row, list) for row in result):
                return result  # Return immediately on first success and validation
            else:
                # Raise error if structure is invalid, triggering retry in main loop
                raise ValueError(f"Parser {parser.__name__} produced invalid structure: {result!r}")

    # Raise an error here if no method works, triggering retry in main loop
    raise ValueError(f"Failed to parse response after all attempts: {response!r}")


# --- End of copied parser.

_GRID_ADAPTER = TypeAdapter(List[List[int]])


def parse_answer(content: str) -> list[list[int]]:
    """``Attempt(answer=content)``: field validator, then ``List[List[int]]``."""
    return _GRID_ADAPTER.validate_python(parse_and_validate_json(content))


class TokenMismatchError(Exception):
    """Same check as ``OpenAIBaseAdapter._calculate_cost`` in the harness."""


def check_token_counts(usage: dict[str, Any]) -> None:
    pt = usage.get("prompt_tokens") or 0
    ct = usage.get("completion_tokens") or 0
    tt = usage.get("total_tokens") or 0
    rt = ((usage.get("completion_tokens_details") or {}).get("reasoning_tokens")) or 0
    if tt == 0 or pt + ct == tt:
        return
    reasoning = rt if rt else tt - (pt + ct)
    computed = pt + ct + reasoning
    if computed != tt:
        raise TokenMismatchError(
            f"Token count mismatch: API reports total {tt}, but computed "
            f"P:{pt} + C:{ct} + R:{reasoning} = {computed}"
        )


class ArcHarnessAgentOptions(AgentOptions):
    max_tokens: int | None = Field(
        default=None, description="max_tokens sent to the API (model config kwarg)."
    )
    temperature: float | None = Field(
        default=None, description="temperature sent to the API, if set."
    )
    stream: bool = Field(
        default=False, description="Use the streaming Chat Completions call."
    )
    retry_attempts: int = Field(
        default=2, description="Calls per attempt in total (harness --retry_attempts)."
    )
    request_timeout_sec: float = Field(
        default=1800.0, description="OpenAI client timeout (harness: 1800)."
    )


class ArcHarnessAgent(BaseAgent):
    """One ARC attempt the way the ARC Prize harness makes it."""

    options_model = ArcHarnessAgentOptions

    @staticmethod
    def name() -> str:
        return AGENT_NAME

    def version(self) -> str:
        return AGENT_VERSION

    async def setup(self, environment: BaseEnvironment) -> None:
        return None

    def _model_id(self) -> str:
        if not self.model_name:
            raise ValueError("A model is required (-m openai/<model>)")
        model = self.model_name
        return model.split("/", 1)[1] if model.startswith("openai/") else model

    def _api_kwargs(self) -> dict[str, Any]:
        opts: ArcHarnessAgentOptions = self.options  # type: ignore[assignment]
        kwargs: dict[str, Any] = {}
        if opts.max_tokens is not None:
            kwargs["max_tokens"] = opts.max_tokens
        if opts.temperature is not None:
            kwargs["temperature"] = opts.temperature
        return kwargs

    async def _call(self, client: Any, model: str, prompt: str) -> dict[str, Any]:
        opts: ArcHarnessAgentOptions = self.options  # type: ignore[assignment]
        messages = [{"role": "user", "content": prompt}]
        if not opts.stream:
            resp = await client.chat.completions.create(
                model=model, messages=messages, **self._api_kwargs()
            )
            message = resp.choices[0].message
            usage = resp.usage.model_dump() if resp.usage else {}
            return {
                "id": resp.id,
                "served_model": resp.model,
                "content": message.content or "",
                "reasoning_content": getattr(message, "reasoning_content", None),
                "finish_reason": resp.choices[0].finish_reason,
                "usage": usage,
            }

        stream = await client.chat.completions.create(
            model=model,
            messages=messages,
            stream=True,
            stream_options={"include_usage": True},
            **self._api_kwargs(),
        )
        content_chunks: list[str] = []
        reasoning_chunks: list[str] = []
        finish_reason = "stop"
        last_chunk = None
        async for chunk in stream:
            last_chunk = chunk
            if chunk.choices:
                delta = chunk.choices[0].delta
                if delta.content:
                    content_chunks.append(delta.content)
                reasoning = getattr(delta, "reasoning_content", None)
                if isinstance(reasoning, str) and reasoning:
                    reasoning_chunks.append(reasoning)
                if chunk.choices[0].finish_reason:
                    finish_reason = chunk.choices[0].finish_reason
        usage = (
            last_chunk.usage.model_dump()
            if last_chunk is not None and getattr(last_chunk, "usage", None)
            else {}
        )
        return {
            "id": last_chunk.id if last_chunk is not None else None,
            "served_model": getattr(last_chunk, "model", None),
            "content": "".join(content_chunks),
            "reasoning_content": "".join(reasoning_chunks) or None,
            "finish_reason": finish_reason,
            "usage": usage,
        }

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        from openai import AsyncOpenAI

        opts: ArcHarnessAgentOptions = self.options  # type: ignore[assignment]
        model = self._model_id()
        client = AsyncOpenAI(
            api_key=os.environ["OPENAI_API_KEY"],
            base_url=os.environ.get("OPENAI_BASE_URL") or None,
            max_retries=0,
            timeout=opts.request_timeout_sec,
        )
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        (self.logs_dir / "prompt.txt").write_text(instruction)

        n_in = n_out = n_cache = 0
        tries: list[dict[str, Any]] = []
        grid: list[list[int]] | None = None
        try:
            for retry in range(opts.retry_attempts):
                record: dict[str, Any] = {
                    "retry": retry + 1,
                    "start": datetime.now(timezone.utc).isoformat(),
                }
                t0 = time.monotonic()
                try:
                    result = await self._call(client, model, instruction)
                    record.update(result)
                    usage = result["usage"] or {}
                    n_in += usage.get("prompt_tokens") or 0
                    n_out += usage.get("completion_tokens") or 0
                    n_cache += (
                        (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
                        or 0
                    )
                    check_token_counts(usage)
                    grid = parse_answer(result["content"].strip())
                    record["parsed"] = True
                except Exception as exc:  # harness: any exception -> next retry
                    record["error"] = f"{type(exc).__name__}: {exc}"[:2000]
                    record["parsed"] = False
                record["seconds"] = round(time.monotonic() - t0, 1)
                tries.append(record)
                (self.logs_dir / f"response-{retry + 1}.json").write_text(
                    json.dumps(record, indent=2, default=str)
                )
                if grid is not None:
                    break
        finally:
            await client.close()
            context.n_input_tokens = n_in
            context.n_output_tokens = n_out
            context.n_cache_tokens = n_cache
            context.metadata = {
                "model": model,
                "served_model": next(
                    (t.get("served_model") for t in tries if t.get("served_model")),
                    None,
                ),
                "calls": len(tries),
                "answer_written": grid is not None,
                "errors": [t["error"] for t in tries if "error" in t],
            }

        if grid is None:
            return  # Like a null attempt in the harness: scored as incorrect.

        answer_file = self.logs_dir / "output.json"
        answer_file.write_text(json.dumps(grid))
        await environment.exec(command="mkdir -p /app")
        await environment.upload_file(answer_file, ANSWER_PATH)
