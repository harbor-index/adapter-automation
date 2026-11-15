"""BFCL function-calling (FC) agent for Harbor.

This agent reproduces how the original BFCL harness queries an OpenAI-compatible
model in function-calling mode for a single-turn entry
(`OpenAICompletionsHandler` + `BaseHandler.inference_single_turn_FC`, BFCL commit
6ea5797):

1. messages = the entry's conversation (`question[0]`), no extra system prompt;
2. tools = the language-hinted function docs converted with BFCL's
   `convert_to_tool(..., GORILLA_TO_OPENAPI, ModelStyle.OPENAI_COMPLETIONS)`
   ("." in function names becomes "_");
3. one `chat.completions.create(messages, model, temperature, store=False, tools)`
   call, retried with BFCL's backoff on rate-limit errors only;
4. the response is decoded like `OpenAICompletionsHandler.decode_ast` (FC):
   each tool call becomes `{name: json.loads(arguments)}`.

The decoded calls are written to /app/result.json, with function names mapped back
to the names in the task (the reverse of step 2; BFCL does the same comparison by
setting `underscore_to_dot=True` for these models). A name that was not among the
tools sent to the API gets the prefix "unknown_tool:", so it never matches, as in
BFCL. If decoding fails, the raw
model text is written instead, which the verifier treats as "no function call",
as BFCL does for an undecodable response.

The agent reads the conversation and the function docs from the two JSON blocks
in the task instruction. It runs on the host and needs OPENAI_API_KEY (and
OPENAI_BASE_URL for a non-OpenAI endpoint).

Usage:
    harbor run -p datasets/bfcl-autoadapter \
        --agent-import-path bfcl_autoadapter.agents.bfcl_fc_agent:BfclFcAgent \
        -m openai/<model> [--ak temperature=0.001]
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from openai import AsyncOpenAI, RateLimitError
from tenacity import AsyncRetrying, retry_if_exception_type, wait_random_exponential

from bfcl_autoadapter.bfcl_upstream import convert_to_openai_tools

RESULT_PATH = "/app/result.json"
BFCL_DEFAULT_TEMPERATURE = 0.001  # `bfcl generate --temperature` default


def _extract_json_block(instruction: str, heading: str) -> Any:
    """Return the JSON in the first ```json block after `heading`.

    JSON is dumped with escaped newlines, so a line that is exactly ``` can only be
    the closing fence.
    """
    lines = instruction.splitlines()
    try:
        start = lines.index(heading)
    except ValueError as e:
        raise ValueError(f"heading {heading!r} not found in the instruction") from e
    i = start + 1
    while i < len(lines) and lines[i].strip() != "```json":
        i += 1
    if i == len(lines):
        raise ValueError(f"no ```json block after {heading!r}")
    j = i + 1
    while j < len(lines) and lines[j] != "```":
        j += 1
    return json.loads("\n".join(lines[i + 1 : j]))


def parse_task_instruction(instruction: str) -> tuple[list[dict], list[dict]]:
    conversation = _extract_json_block(instruction, "## Conversation")
    functions = _extract_json_block(instruction, "## Available functions")
    return conversation, functions


# Prefix for a called name that was not one of the tools sent to the API. BFCL
# compares such a name with the renamed ("." -> "_") function names, so it can never
# match; the prefix keeps it from matching an original (dotted) name in Harbor. The
# entry still counts as a function call, as upstream.
UNKNOWN_TOOL_PREFIX = "unknown_tool:"


def map_tool_name(name: str, name_map: dict[str, str]) -> str:
    """Map an API tool name back to the task's function name."""
    if name in name_map:
        return name_map[name]
    return UNKNOWN_TOOL_PREFIX + name


def decode_fc_response(model_responses: Any, name_map: dict[str, str]) -> Any:
    """`OpenAICompletionsHandler.decode_ast` for FC models, plus the name mapping.

    Returns the decoded list of calls, or the raw response if decoding fails.
    """
    try:
        decoded_output = []
        for invoked_function in model_responses:
            name = list(invoked_function.keys())[0]
            params = json.loads(invoked_function[name])
            decoded_output.append({map_tool_name(name, name_map): params})
        return decoded_output
    except Exception:
        return model_responses if isinstance(model_responses, (str, type(None))) else str(model_responses)


class BfclFcAgent(BaseAgent):
    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        temperature: float | str = BFCL_DEFAULT_TEMPERATURE,
        *args,
        **kwargs,
    ):
        super().__init__(logs_dir, model_name, *args, **kwargs)
        self.temperature = float(temperature)

    @staticmethod
    def name() -> str:
        return "bfcl-fc-agent"

    def version(self) -> str:
        return "1.0.0"

    async def setup(self, environment: BaseEnvironment) -> None:
        return None

    def _api_model_name(self) -> str:
        if not self.model_name:
            raise ValueError("bfcl-fc-agent needs a model (-m openai/<model>)")
        name = self.model_name
        if name.startswith("openai/"):
            name = name[len("openai/") :]
        return name

    async def _create(self, client: AsyncOpenAI, **kwargs):
        # BFCL: @retry_with_backoff(error_type=RateLimitError), i.e. tenacity with
        # wait_random_exponential(min=6, max=120) and no stop condition.
        async for attempt in AsyncRetrying(
            wait=wait_random_exponential(min=6, max=120),
            retry=retry_if_exception_type(RateLimitError),
            reraise=True,
        ):
            with attempt:
                start = time.time()
                response = await client.chat.completions.create(**kwargs)
                return response, time.time() - start

    async def _write_result(self, environment: BaseEnvironment, answer: Any) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(answer, f, ensure_ascii=False)
            tmp_path = f.name
        try:
            await environment.exec("mkdir -p /app")
            await environment.upload_file(tmp_path, RESULT_PATH)
        finally:
            os.unlink(tmp_path)

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        conversation, functions = parse_task_instruction(instruction)
        tools = convert_to_openai_tools(functions)
        name_map = {re.sub(r"\.", "_", f["name"]): f["name"] for f in functions}

        request: dict[str, Any] = {
            "messages": conversation,
            "model": self._api_model_name(),
            "temperature": self.temperature,
            "store": False,
        }
        if len(tools) > 0:
            request["tools"] = tools

        log: dict[str, Any] = {"request": request}
        log_path = self.logs_dir / "bfcl_fc_agent.json"
        self.logs_dir.mkdir(parents=True, exist_ok=True)

        client = AsyncOpenAI(
            api_key=os.environ.get("OPENAI_API_KEY"),
            base_url=os.environ.get("OPENAI_BASE_URL") or None,
        )
        try:
            response, latency = await self._create(client, **request)
        except Exception as e:
            # BFCL records "Error during inference: ..." as the model result. Write the
            # same, then fail the trial so that it can be retried as an infra error.
            answer = f"Error during inference: {e}"
            log["error"] = repr(e)
            log["result_written"] = answer
            await self._write_result(environment, answer)
            log_path.write_text(json.dumps(log, indent=2, default=str))
            raise
        finally:
            await client.close()

        message = response.choices[0].message
        try:
            model_responses = [
                {func_call.function.name: func_call.function.arguments}
                for func_call in message.tool_calls
            ]
        except Exception:
            model_responses = message.content

        answer = decode_fc_response(model_responses, name_map)
        await self._write_result(environment, answer)

        usage = response.usage
        context.n_input_tokens = getattr(usage, "prompt_tokens", None)
        context.n_output_tokens = getattr(usage, "completion_tokens", None)
        context.metadata = {
            "served_model": response.model,
            "latency_sec": latency,
            "n_tool_calls": len(message.tool_calls or []),
        }
        log.update(
            {
                "served_model": response.model,
                "latency_sec": latency,
                "model_responses": model_responses,
                "reasoning_content": getattr(message, "reasoning_content", None),
                "result_written": answer,
                "usage": usage.model_dump() if usage is not None else None,
                "response": response.model_dump(),
            }
        )
        log_path.write_text(json.dumps(log, indent=2, default=str))
