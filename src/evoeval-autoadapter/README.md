## EvoEval → Harbor Adapter

## Overview

This adapter converts **EvoEval** ([code](https://github.com/evo-eval/evoeval), [paper](https://arxiv.org/abs/2403.19114), [website](https://evo-eval.github.io)) into Harbor tasks.

- **Task type:** Python function completion. Each task gives a function signature with a docstring (the EvoEval `prompt`); the agent writes the completed code to `/workspace/solution.py`.
- **Dataset size:** 100 tasks — all 100 problems of `EvoEval_difficult` (`EvoEval/0` … `EvoEval/99`) from the EvoEval v0.1.0 data release. No problem is excluded.
- **Grading:** in a separate verifier container, the original EvoEval checker (`evoeval==0.1.0`, `evalplus==0.2.0`) runs the solution on the problem's test inputs and compares each output with the canonical solution's output (with EvoEval's special checkers where the original has them). Reward is 1 if all inputs pass, else 0; the dataset mean is EvoEval's pass@1 (one greedy sample per problem).
- **Oracle:** the canonical solutions score 100/100 (1.000) with the Harbor oracle agent on Daytona, and 100/100 with the original `evoeval.evaluate`.
- **Other datasets:** `--dataset` also converts `EvoEval_creative`, `EvoEval_subtle`, `EvoEval_combine` and `EvoEval_tool_use` (100 problems each), which use the same grading path. The registered dataset and the parity experiment cover `EvoEval_difficult` only.
- **Main adaptation:** the original harness sends the prompt to an LLM and post-processes the reply; here the agent writes a file. The instruction keeps the original request sentence and the prompt verbatim and adds where to write the file. See [Notes & Caveats](#notes--caveats).
- **License:** EvoEval is released under Apache-2.0.

## What is EvoEval?

EvoEval (Xia, Deng and Zhang, 2024) is a set of program-synthesis benchmarks made by *evolving* HumanEval problems with an LLM. It has 828 problems in 7 benchmarks: 5 semantic-altering ones (Difficult, Creative, Subtle, Combine, Tool_Use) and 2 semantic-preserving ones (Verbose, Concise). `EvoEval_difficult` makes each HumanEval problem harder by adding constraints and requirements, replacing common requirements with less common ones, or adding reasoning steps. Every problem has a canonical solution and test inputs; a sample is correct when its outputs match the canonical solution's outputs on all inputs. The paper reports pass@1 of greedy decoding for more than 50 LLMs, and shows that models drop sharply from HumanEval to EvoEval.

## Adapter Features

- **Data from the release.** Downloads `<dataset>.jsonl.gz` from the EvoEval v0.1.0 release (the version pinned by `evoeval==0.1.0`), or reads a local copy with `--data-file`. Task names are stable: `evo-eval/evoeval-autoadapter-<subset>-<n>`, where `<n>` is the number in the upstream ID `EvoEval/<n>`.
- **Separate verifier.** `[verifier].environment_mode = "separate"`: grading runs in a fresh container built from the same image, and only `/workspace/solution.py` is copied over from the agent's container (`artifacts = ["/workspace/solution.py"]`). The agent's container holds neither the grader nor its packages, so the agent cannot change them. `tests/test.sh` resets the reward to 0, installs the pinned grader packages, and runs `tests/evaluate.py`, with isolated Python (`python3 -I`) from a directory outside `/workspace`.
- **Original grading code.** `tests/evaluate.py` imports the original functions: `evalplus.gen.util.trusted_exec` computes the expected outputs from `prompt + "\n" + canonical_solution`, and `evoeval.eval_test.untrusted_check` runs the solution with the same arguments as `evoeval.evaluate` (`atol=0`, fast check, `--min-time-limit 1`, `--gt-time-limit-factor 4.0`, the 4 GB memory guard and EvoEval's special checkers, e.g. for `EvoEval/10` and `EvoEval/32`). `/logs/verifier/details.json` records the status and the first failing input index.
- **Pinned environment.** `python:3.9.25-slim-bookworm` (EvoEval's evaluation image uses Python 3.9). The verifier installs `evoeval==0.1.0`, `evalplus==0.2.0` and their dependencies at pinned versions.
- **Oracle.** `solution/solution.py` is `prompt + "\n" + canonical_solution`, the code that the original ground truth executes.
- **Parity agent `evoeval-codegen` (custom).** `src/evoeval_autoadapter/codegen_agent.py` ports EvoEval's own API harness to Harbor: one chat completion with the original system message ("You are a helpful assistant.") and user message ("Please complete the following code snippet.\n```\n{prompt}\n```"), `temperature=0`, `n=1`, `max_tokens` = prompt tokens + `max_new_tokens`, the retry loop of `request_chatgpt_engine`, and then the original `tool/sanitize.py --eof` post-processing, verbatim. It reads the prompt from the instruction and writes the result to `/workspace/solution.py`. Options (`--ak`): `max_new_tokens` (default 32768), `request_timeout_sec` (default 900), `temperature` (default 0).
- **Standard agents.** Any Harbor agent can solve the tasks (for example `terminus-2`).

## Generated Task Structure

```
evoeval-autoadapter/
├── evoeval-autoadapter-difficult-<n>/
│   ├── task.toml                 # name evo-eval/evoeval-autoadapter-difficult-<n>, authors, metadata, limits
│   ├── instruction.md            # original request sentence + prompt + output location
│   ├── environment/
│   │   └── Dockerfile            # python:3.9.25-slim-bookworm (agent and verifier image)
│   ├── solution/
│   │   ├── solve.sh              # copies solution.py to /workspace/solution.py
│   │   └── solution.py           # prompt + canonical_solution
│   └── tests/
│       ├── test.sh               # installs pinned evoeval/evalplus, runs evaluate.py, writes reward.txt
│       ├── evaluate.py           # original EvoEval checker for one problem
│       └── problem.json          # dataset, task_id, entry_point, prompt, canonical_solution, inputs
```

Adapter code:

```
src/evoeval-autoadapter/
├── README.md
├── adapter_metadata.json
├── parity_experiment.json
├── pyproject.toml
├── uv.lock
├── run_evoeval-autoadapter.yaml          # oracle (default) / other agents
├── run_evoeval-autoadapter_parity.yaml   # Harbor-side parity run (evoeval-codegen, Daytona)
└── src/evoeval_autoadapter/
    ├── __init__.py
    ├── adapter.py
    ├── main.py
    ├── codegen_agent.py                  # parity agent (EvoEval's API harness)
    └── task-template/
        ├── task.toml
        ├── instruction.md
        ├── environment/Dockerfile
        ├── solution/solve.sh
        └── tests/{test.sh,evaluate.py}
```

## Run Evaluation / Harness

### Running with Datasets Registry

After the dataset is published:

```bash
# Oracle (canonical solutions)
harbor run -d evo-eval/evoeval-autoadapter

# A specific agent and model
harbor run -d evo-eval/evoeval-autoadapter -a <agent_name> -m "<model_name>"
```

### Using Job Configurations

From the repository root, after generating the tasks (see [Usage](#usage-create-task-directories)):

```bash
# Oracle on the full dataset
harbor run -c src/evoeval-autoadapter/run_evoeval-autoadapter.yaml

# Or point at the local dataset directly
harbor run -p datasets/evoeval-autoadapter -a <agent_name> -m "<model_name>"

# Parity agent (EvoEval's API harness) on Daytona, with the Harbor version used for parity
PYTHONPATH=src/evoeval-autoadapter/src \
  uvx --python 3.12 --from "harbor[daytona,adapter]==0.25.0" harbor run \
  -c src/evoeval-autoadapter/run_evoeval-autoadapter_parity.yaml --job-name <name>

# Resume a job
harbor job resume -p jobs/<job_name>
```

Results are written to `jobs/`.

### Running Individual Trial

```bash
harbor trial start -p datasets/evoeval-autoadapter/evoeval-autoadapter-difficult-10
harbor trial start -p datasets/evoeval-autoadapter/evoeval-autoadapter-difficult-10 -a <agent_name> -m "<model_name>"
```

## Usage: Create Task Directories

```bash
cd src/evoeval-autoadapter
uv sync
uv run evoeval-autoadapter --output-dir ../../datasets/evoeval-autoadapter
```

Flags:
- `--output-dir` — where to write tasks (default: `datasets/evoeval-autoadapter` at the repository root)
- `--limit N` — only the first N problems (upstream order)
- `--overwrite` — replace existing task directories
- `--task-ids ...` — only these problems (`EvoEval/10`, `10`, or a task name such as `evoeval-autoadapter-difficult-10`)
- `--dataset` — `EvoEval_difficult` (default), `EvoEval_creative`, `EvoEval_subtle`, `EvoEval_combine` or `EvoEval_tool_use`
- `--data-file` — local `<dataset>.jsonl(.gz)` instead of downloading the release file

## Comparison with Original Benchmark (Parity)

Parity compares EvoEval's own pipeline with the Harbor adapter, using the same model and settings on both sides:

- **Original side:** the original `codegen/generate.py` (greedy, `OpenAIChatDecoder`), `tool/sanitize.py --eof` and `evoeval/evaluate.py`, from the fork [MrLYG/evoeval@harbor-parity-autoadapter](https://github.com/MrLYG/evoeval/tree/harbor-parity-autoadapter).
- **Harbor side:** the `evoeval-codegen` agent (the same harness ported to Harbor) on the generated tasks, on Daytona.

| Agent | Model | Metric | Number of Runs | Dataset Size | Original Benchmark Performance | Harbor Adapter Performance |
|-------|-------|--------|------------------|--------------|------------------------------|----------------------------|
| evoeval-codegen@1.0.0 | deepseek-v4.1-flash | pass@1 (%) | 3 | 100 (100%) | 58.00 ± 0.58 | 58.33 ± 0.33 |

Scores are mean ± sample SEM over runs. Runs: original 58, 57, 59; Harbor 58, 58, 59. The run ranges overlap. No task scored 3/3 on one side and 0/3 on the other; the 19 tasks that changed between runs changed on both sides. Raw runs are in `parity_experiment.json`.

Settings on both sides: served model `deepseek-v4-1-flash-261017`, temperature 0, `max_tokens` = prompt tokens + 32768, 900-second request limit, prompts with LF line endings, `openai==3.46.0`, 15 concurrent requests per side, Harbor 0.25.0 on Daytona. See [Notes & Caveats](#notes--caveats).

**Standard CLI agent check (Harbor only).** `terminus-2@2.0.0` (Harbor 0.25.0) with `openai/deepseek-v4.1-flash`, 1 run on all 100 tasks: 67/100 (67.0%), no infrastructure errors; 2 trials hit the 1800-second agent timeout and scored 0. EvoEval has no agent harness, so there is no original-side number for this agent.

Reproduce the original side (fork, branch `harbor-parity-autoadapter`):

```bash
git clone -b harbor-parity-autoadapter https://github.com/MrLYG/evoeval.git && cd evoeval
# generation env (Python 3.12) and evaluation env (Python 3.9, as EvoEval's evaluation image)
uv venv .venv-gen --python 3.12
VIRTUAL_ENV=$PWD/.venv-gen uv pip install openai==3.46.0 tiktoken==0.14.0 rich==15.0.0 evalplus==0.2.0 .
uv venv .venv-eval --python 3.9
VIRTUAL_ENV=$PWD/.venv-eval uv pip install evalplus==0.2.0 numpy==2.0.2 .
export OPENAI_API_KEY=... OPENAI_BASE_URL=...   # endpoint that serves deepseek-v4.1-flash
GEN_PY=$PWD/.venv-gen/bin/python EVAL_PY=$PWD/.venv-eval/bin/python \
  harbor_parity/run_original_parity.sh runs/run1 15   # 15 parallel requests; new directory per run: run2, run3
# -> runs/run1/summary.json (pass@1 and per-task status)
```

The fork's README lists every change against `evo-eval/evoeval@d5ca310` and the exact settings.

Reproduce the Harbor side (from this repository's root), with the Harbor version used for the experiment:

```bash
cd src/evoeval-autoadapter && uv run evoeval-autoadapter --output-dir ../../datasets/evoeval-autoadapter && cd ../..
PYTHONPATH=src/evoeval-autoadapter/src \
  uvx --python 3.12 --from "harbor[daytona,adapter]==0.25.0" harbor run \
  -c src/evoeval-autoadapter/run_evoeval-autoadapter_parity.yaml --job-name parity-run1   # repeat: run2, run3
```

The config runs 15 concurrent trials on Daytona, as in the experiment (the original side also used 15 parallel requests). Inside the adapter environment, `uv run --group parity harbor ...` runs the locked Harbor 0.25.0 instead.

The Harbor score of a run is the mean reward over the 100 tasks (×100 for %), which equals EvoEval's pass@1 for one greedy sample.

Links:
- Original benchmark: https://github.com/evo-eval/evoeval
- Original-side fork: https://github.com/MrLYG/evoeval/tree/harbor-parity-autoadapter (changes against upstream: https://github.com/evo-eval/evoeval/compare/main...MrLYG:evoeval:harbor-parity-autoadapter)
- Adapter PR: https://github.com/harbor-index/adapter-automation/pull/3
- Dataset PR: https://github.com/harbor-framework/harbor-datasets/pull/273
- Parity results PR: https://huggingface.co/datasets/harborframework/parity-experiments/discussions/277 (no files yet: the Hub repository is over its file limit and refuses new files)

## Notes & Caveats

- **Request time limit.** The original sets `signal.alarm(900)` around each request (a timed-out request is aborted and retried); `evoeval-codegen` cancels each request after the same 900 seconds (`asyncio.wait_for`) with the same retry rules. Both sides use the `openai` SDK client defaults (`openai==3.46.0`).
- **Verbatim sanitizer.** The ported `tool/sanitize.py` code keeps its bare `except` on purpose, so that it behaves exactly like the original.
- **Reasoning-model settings (both sides).** The parity model is a reasoning model; its reasoning tokens count toward `max_tokens`. With EvoEval's default of 512 new tokens, most replies end before any answer, so both sides use `max_new_tokens=32768`. EvoEval's 100-second per-request alarm is raised to 900 seconds on both sides, since a long reasoning request would otherwise be retried forever. No other generation setting changes.
- **Fork changes.** The fork only makes the API-only path runnable without GPU packages (optional imports of torch, transformers, vllm, anthropic and google-generativeai), adds a `deepseek-v4.1-flash` model entry, makes the request time limit configurable, and adds `harbor_parity/` (runner and summary). The prompt, the sanitizer and the grading code are unchanged.
- **Line endings.** Ten `EvoEval_difficult` prompts (`EvoEval/15, 18, 35, 42, 43, 54, 62, 68, 69, 93`) use CRLF line endings in the release data. Harbor reads `instruction.md` as text, so agents get these prompts with LF line endings. For parity, the original side sends them with LF too (`EVOEVAL_PROMPT_LF=1` in the fork), so both sides send the same request. The text is otherwise identical. The grader uses the original prompt bytes.
- **File instead of reply.** The original harness extracts code from the model's reply; Harbor agents write `/workspace/solution.py` themselves. The whole file is executed, so helper functions outside the target function are kept (the original sanitizer drops them). The `evoeval-codegen` agent applies the original sanitizer, so the parity runs are not affected.
- **Python 3.9.** Solutions run on Python 3.9, as in EvoEval's evaluation image; newer syntax (for example `match`, or `int | None` in annotations without `from __future__ import annotations`) fails. The instruction says so.
- **Verifier network access.** `tests/test.sh` installs the pinned grader packages from PyPI in the verifier container, so verification needs network access. The packages are not in the image so that the agent cannot see or change them.
- **Checker isolation (inherited).** As in EvoEval, the candidate runs in a child process of the checker under `reliability_guard`, and the expected outputs are in that process's memory. `evaluate.py` deletes `/tests/problem.json` before running the candidate, but deliberately malicious code could still inspect memory; the original checker has the same property, and the grading logic is kept unchanged.
- **Timing limits.** EvoEval sets a time limit per test input from the canonical solution's run time (at least 1 s, 4× the reference time). The verifier measures the reference time inside the same container, so slow machines scale both sides.
- **Not supported:** `EvoEval_verbose` and `EvoEval_concise` use HumanEval+ base/plus inputs and a different grading path; this adapter does not convert them.
- **Temperature 0 is not fully deterministic** with the parity model, so runs differ slightly on both sides.

## Installation / Prerequisites

- Harbor (`uv tool install harbor`), tested with Harbor 0.25.0. For exact reproduction use `uvx --python 3.12 --from "harbor[daytona,adapter]==0.25.0" harbor ...`, or `uv sync --group parity` and `uv run --group parity harbor ...` in this folder.
- Docker for local runs, or a Daytona account (`DAYTONA_API_KEY`) for `-e daytona`.
- Python 3.12+ and `uv`:
  ```bash
  cd src/evoeval-autoadapter
  uv sync                    # task generation (standard library only)
  uv sync --group parity     # also installs Harbor, openai and tiktoken for the parity agent
  ```
- For model runs: `OPENAI_API_KEY`, and `OPENAI_BASE_URL` for an OpenAI-compatible endpoint.
- Task generation downloads about 80 KB from GitHub; each task image installs the pinned packages from PyPI.

## Troubleshooting

- **All rewards 0 with `base_status: "fail"` and `solution_missing: true`:** the agent did not write `/workspace/solution.py`.
- **`TypeError` in solutions that use `X | None` or `match`:** the grader runs Python 3.9; see Notes & Caveats.
- **`evoeval-codegen` returns empty solutions:** the model hit `max_tokens` while reasoning; raise `--ak max_new_tokens=...`. `agent/raw_completion.txt` and `agent/reasoning.txt` in the trial directory show the reply.
- **`ModuleNotFoundError: evoeval_autoadapter`:** set `PYTHONPATH=src/evoeval-autoadapter/src` when using the parity agent.
- **Reward 0 with no `details.json`:** the verifier could not install the grader packages; see `verifier/pip.log` (the verifier container needs network access to PyPI).

## Citation

```bibtex
@article{evoeval,
  author    = {Xia, Chunqiu Steven and Deng, Yinlin and Zhang, Lingming},
  title     = {Top Leaderboard Ranking = Top Coding Proficiency, Always? EvoEval: Evolving Coding Benchmarks via LLM},
  year      = {2024},
  journal   = {arXiv preprint},
  url       = {https://arxiv.org/abs/2403.19114}
}
```

## Authors & Contributions

This adapter is developed and maintained by [Yuangang Li](mailto:liyuangang66@gmail.com).

The benchmark, its data and its evaluation code are by Chunqiu Steven Xia, Yinlin Deng and Lingming Zhang ([evo-eval/evoeval](https://github.com/evo-eval/evoeval)).

**Issues and Contributions:**
- Submit issues and pull requests to the adapters repository.
