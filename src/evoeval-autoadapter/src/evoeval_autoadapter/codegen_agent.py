"""Harbor port of EvoEval's original code-generation harness, used for parity.

EvoEval has no agent: the paper scores one greedy completion per problem from a
chat model. For API models the original pipeline is

1. `codegen/generate.py` with `OpenAIChatDecoder` (codegen/model.py): one chat
   request with the system message "You are a helpful assistant." and the user
   message "Please complete the following code snippet.\\n```\\n{prompt}\\n```",
   `temperature=0`, `n=1`, `max_tokens = prompt tokens + max_new_tokens`, retried
   by `evoeval.util.api_request.request_chatgpt_engine`;
2. `tool/sanitize.py --eof`, which keeps the prompt's preamble (imports and given
   helpers) plus the model's version of the target function;
3. `evoeval/evaluate.py`.

This agent performs steps 1 and 2 with the same prompt, parameters and
sanitizer, and writes the result to `/workspace/solution.py`; the task's
verifier performs step 3. The only parameter changes are the two that the
original-side fork (`harbor_parity/run_original_parity.sh`) applies as well:
`max_new_tokens` 512 -> 32768 (reasoning tokens count toward `max_tokens`) and
the per-request time limit 100 s -> 900 s. The fork also sends the prompt with
LF line endings (`EVOEVAL_PROMPT_LF=1`), because Harbor reads `instruction.md`
as text and agents therefore receive LF.

Usage:
    PYTHONPATH=src/evoeval-autoadapter/src harbor run -p datasets/evoeval-autoadapter \\
        --agent-import-path evoeval_autoadapter.codegen_agent:EvoEvalCodegenAgent \\
        -m openai/deepseek-v4.1-flash
"""

from __future__ import annotations

import ast
import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

# --- codegen/model.py (OpenAIChatDecoder) and evoeval/util/api_request.py ----------
GENERATION_TEMPLATE = "Please complete the following code snippet.\n```\n{prompt}\n```"
SYSTEM_MESSAGE = "You are a helpful assistant."

# --- tool/sanitize.py ---------------------------------------------------------------
INCODER_EXTRA = ["</code>", "<|", "</CODE>"]
POLYCODER_EXTRA = ["\n//", "\n/*"]
NON_CODE_EOFS = ["<|endoftext|>", "\n```", "\n</s>", "\n#"]

PROMPT_BLOCK_RE = re.compile(r"^```python\n(.*?)\n```$", re.DOTALL | re.MULTILINE)
SOLUTION_PATH = "/workspace/solution.py"


def num_tokens_from_messages(message: str, model: str) -> int:
    """evoeval.util.api_request.num_tokens_from_messages for a single message."""
    import tiktoken

    try:
        encoding = tiktoken.encoding_for_model(model)
    except KeyError:
        encoding = tiktoken.get_encoding("cl100k_base")
    return len(encoding.encode(message))


def extract_function(code: str, target_func: str) -> str:
    """tool/sanitize.py extract_function, verbatim."""

    def remove_last_line_until_parse(code):
        try:
            tree = ast.parse(code)
        except:  # noqa: E722 - verbatim
            if "\n" in code:
                code = code.rsplit("\n", 1)[0]
                return remove_last_line_until_parse(code)
            else:
                return None
        return tree

    tree = remove_last_line_until_parse(code)
    if tree is None:  # fail to parse
        return ""

    # return the target function only
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            if node.name == target_func:
                return ast.unparse(node)
    return ""


def to_four_space_indents(old_code: str) -> str:
    new_code = ""
    for line in old_code.splitlines():
        lspace = len(line) - len(line.lstrip())
        if lspace == 3:
            new_code += " "
        new_code += line + "\n"
    return new_code


def sanitize(old_code: str, prompt: str, entry_point: str, folder: str, eof: bool = True) -> str:
    """The per-file body of tool/sanitize.py `sanitize_folder` (run with --eof)."""
    def_left = "def " + entry_point + "("

    imports = prompt.split(def_left)[0]

    new_code = imports + def_left + old_code.split(def_left)[-1]
    chunks = new_code.split(def_left)  # imports + def_left + {def_right + impl}

    if len(chunks) == 2:
        new_code = def_left + chunks[-1]  # fn + impl

    if "chatgpt" in folder:
        tmp = ""
        for line in new_code.splitlines():
            if line.strip() == "python":
                continue
            tmp += line + "\n"
        new_code = tmp

    new_code = to_four_space_indents(new_code)

    if eof:
        eof_strs = NON_CODE_EOFS
        if "incoder" in folder:
            eof_strs = eof_strs + INCODER_EXTRA
        if "polycoder" in folder:
            eof_strs = eof_strs + POLYCODER_EXTRA
        if "mistral" in folder:
            eof_strs = eof_strs + [r"</s>"]
        for eof_str in eof_strs:
            new_code = new_code.split(eof_str)[0]

    new_code = extract_function(new_code, entry_point)

    if len(chunks) == 2:
        new_code = chunks[0] + new_code
    return new_code


def parse_instruction(instruction: str) -> tuple[str, str]:
    """Recover the EvoEval prompt and entry point from the task instruction."""
    match = PROMPT_BLOCK_RE.search(instruction)
    if match is None:
        raise ValueError("instruction.md has no ```python block with the EvoEval prompt")
    prompt = match.group(1)
    entry = re.search(r"Keep the signature of `([^`]+)`", instruction)
    if entry is None:
        raise ValueError("instruction.md does not name the entry point")
    return prompt, entry.group(1)


class EvoEvalCodegenAgent(BaseAgent):
    """One greedy chat completion + the original sanitizer (see module docstring)."""

    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        max_new_tokens: int | str = 32768,
        request_timeout_sec: float | str = 900,
        temperature: float | str = 0.0,
        *args: Any,
        **kwargs: Any,
    ):
        super().__init__(logs_dir, model_name, *args, **kwargs)
        self.max_new_tokens = int(max_new_tokens)
        self.request_timeout_sec = float(request_timeout_sec)
        self.temperature = float(temperature)

    @staticmethod
    def name() -> str:
        return "evoeval-codegen"

    def version(self) -> str:
        return "1.0.0"

    async def setup(self, environment: BaseEnvironment) -> None:
        return None

    # The api model id, e.g. "openai/deepseek-v4.1-flash" -> "deepseek-v4.1-flash".
    def _api_model(self) -> str:
        if not self.model_name:
            raise ValueError("evoeval-codegen needs a model (-m openai/<model>)")
        return self.model_name.split("/", 1)[1] if "/" in self.model_name else self.model_name

    async def _request(self, client: Any, config: dict[str, Any]) -> Any:
        """evoeval.util.api_request.request_chatgpt_engine.

        The original arms `signal.alarm(timeout)` around each blocking call, which
        aborts the request after `timeout` seconds and lands in the catch-all branch
        (sleep 1 s, retry). Here `asyncio.wait_for` cancels the request after the
        same time, which closes its connection. Both sides use the `openai` SDK
        client defaults (timeout, max_retries).
        """
        import openai

        ret = None
        while ret is None:
            try:
                ret = await asyncio.wait_for(
                    client.chat.completions.create(**config), timeout=self.request_timeout_sec
                )
            except asyncio.TimeoutError:
                self.logger.warning("Unknown error. Waiting... end of time")
                await asyncio.sleep(1)
            except openai.BadRequestError as e:
                self.logger.warning("BadRequestError: %s", e)
            except openai.RateLimitError as e:
                self.logger.warning("Rate limit exceeded. Waiting... %s", e)
                await asyncio.sleep(5)
            except openai.APIConnectionError as e:
                self.logger.warning("API connection error. Waiting... %s", e)
                await asyncio.sleep(5)
            except Exception as e:  # noqa: BLE001 - mirrors the original catch-all
                self.logger.warning("Unknown error. Waiting... %s", e)
                await asyncio.sleep(1)
        return ret

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        import openai

        prompt, entry_point = parse_instruction(instruction)
        model = self._api_model()
        message = GENERATION_TEMPLATE.format(prompt=prompt.strip())
        config = {
            "model": model,
            "max_tokens": num_tokens_from_messages(message, model) + self.max_new_tokens,
            "temperature": self.temperature,
            "n": 1,
            "messages": [
                {"role": "system", "content": SYSTEM_MESSAGE},
                {"role": "user", "content": message},
            ],
        }

        # OPENAI_API_KEY / OPENAI_BASE_URL from the environment, SDK defaults as in the original.
        client = openai.AsyncOpenAI()
        started = time.time()
        try:
            ret = await self._request(client, config)
        finally:
            await client.close()
        elapsed = time.time() - started

        choice = ret.choices[0]
        raw = choice.message.content or ""
        # generate.py writes the raw text and sanitize.py reads it back in text mode.
        raw_as_read = raw.replace("\r\n", "\n").replace("\r", "\n")
        folder = f"{model.lower()}_temp_{self.temperature}"
        solution = sanitize(raw_as_read, prompt, entry_point, folder, eof=True)

        self.logs_dir.mkdir(parents=True, exist_ok=True)
        (self.logs_dir / "request.json").write_text(json.dumps(config, indent=2))
        (self.logs_dir / "raw_completion.txt").write_text(raw)
        reasoning = getattr(choice.message, "reasoning_content", None)
        if reasoning is None and getattr(choice.message, "model_extra", None):
            reasoning = choice.message.model_extra.get("reasoning_content")
        if reasoning:
            (self.logs_dir / "reasoning.txt").write_text(reasoning)
        local_solution = self.logs_dir / "solution.py"
        local_solution.write_text(solution)

        await environment.exec("mkdir -p /workspace")
        await environment.upload_file(local_solution, SOLUTION_PATH)

        usage = getattr(ret, "usage", None)
        context.n_input_tokens = getattr(usage, "prompt_tokens", None)
        context.n_output_tokens = getattr(usage, "completion_tokens", None)
        context.metadata = {
            "served_model": getattr(ret, "model", None),
            "finish_reason": choice.finish_reason,
            "request_seconds": round(elapsed, 2),
            "raw_completion_chars": len(raw),
            "sanitized_chars": len(solution),
        }
