## BFCL (Berkeley Function Calling Leaderboard) → Harbor Adapter

## Overview

This adapter turns the **single-turn categories of BFCL v4** into Harbor tasks. Each task gives the agent a conversation and a list of function definitions; the agent must write the function call(s) that answer the last user message (or no call, when no function fits) to `/app/result.json`. The verifier grades the file with BFCL's own AST checker and relevance check.

- **Source:** [ShishirPatil/gorilla › berkeley-function-call-leaderboard](https://github.com/ShishirPatil/gorilla/tree/main/berkeley-function-call-leaderboard), pinned to commit `6ea57973c7a6097fd7c5915698c54c17c5b1b6c8`. Data and code are Apache-2.0.
- **Scope:** the 13 single-turn categories (non-live and live). Multi-turn, web-search, memory and format-sensitivity categories are not part of this adapter.
- **Size:** BFCL has 3641 entries in these categories. 6 entries cannot be passed by any answer under the original checker (their ground truth and function doc disagree) and are left out, so the adapter produces **3635 tasks** (see [Notes & Caveats](#notes--caveats)).
- **Main adaptation:** the original harness reads the model's API response; here the agent writes the same information (a JSON list of `{function_name: {parameter: value}}`) to a file. The task text is BFCL's own default prompt plus a short answer-format section.

| Group | Category | BFCL entries | Tasks |
|---|---|---:|---:|
| non-live | simple_python | 400 | 400 |
| non-live | simple_java | 100 | 98 |
| non-live | simple_javascript | 50 | 50 |
| non-live | multiple | 200 | 200 |
| non-live | parallel | 200 | 200 |
| non-live | parallel_multiple | 200 | 200 |
| non-live | irrelevance | 240 | 240 |
| live | live_simple | 258 | 256 |
| live | live_multiple | 1053 | 1051 |
| live | live_parallel | 16 | 16 |
| live | live_parallel_multiple | 24 | 24 |
| live | live_irrelevance | 884 | 884 |
| live | live_relevance | 16 | 16 |
| | **Total** | **3641** | **3635** |

## What is BFCL?

The [Berkeley Function Calling Leaderboard](https://gorilla.cs.berkeley.edu/leaderboard.html) (BFCL, ICML 2025: [OpenReview](https://openreview.net/forum?id=2GmDdhBdDk), [poster page](https://icml.cc/virtual/2025/poster/46593)) measures how well large language models call functions (tools). In the single-turn categories the model gets one request and a set of function docs (Python, Java or JavaScript) and must produce the right calls: one call (`simple`), one call chosen among several functions (`multiple`), several calls of one function (`parallel`), or several calls of several functions (`parallel_multiple`). The `irrelevance` categories check that the model makes no call when no function fits; `live_relevance` checks that it makes at least one call. "Live" entries come from real user-contributed requests. Scoring is deterministic: BFCL parses the calls and compares the function names, parameter names, types and values with a list of accepted values (AST checking). Each entry is correct or incorrect; BFCL reports accuracy per category.

## Adapter Features

- **Pinned upstream data.** The adapter fetches the BFCL data at the pinned commit (sparse `git fetch` into a temporary folder), or reads an existing checkout with `--bfcl-root`.
- **Same inputs as the original.** The function docs in the instruction include BFCL's language-specific hints (for example "Note that the provided function is in Java 8 SDK syntax." and the "in string representation" notes for Java/JavaScript parameters), exactly as BFCL shows them to the model at generation time. The conversation keeps all roles (some live entries have a system message or earlier turns).
- **Original grading code.** `tests/bfcl_checker/` is BFCL's `ast_checker.py`, the Java/JavaScript type converters, the type mappings and the output-format checks, copied unchanged except for import lines. `tests/evaluate.py` follows BFCL's `_evaluate_single_ast_entry` and `_evaluate_single_relevance_entry`. Checked against the installed upstream package on all tasks: same verdict on the oracle answer and on 3 perturbed answers per task.
- **Oracle solutions.** BFCL has no reference outputs, only accepted values. The adapter builds one answer per task from the ground truth and checks it with the BFCL checker while generating the task.
- **Category selection.** `--categories` takes BFCL categories or the groups `non_live`, `live`, `single_turn`/`all`.
- **One shared environment.** All tasks use the same small image (`python:3.12-slim-bookworm` + `curl`), so on Daytona one snapshot serves every task (`--ek auto_snapshot=true`).
- **Isolated grading.** The verifier runs in a fresh container built from the same image (`[verifier].environment_mode = "separate"`); Harbor copies only the declared artifact `/app/result.json` into it. The ground truth (`tests/`) never enters the agent's container, so nothing the agent leaves running can read it or touch the reward. Inside the agent container the agent runs as the unprivileged user `agent` (`[agent].user`); the verifier runs as root with `/usr/local/bin/python3 -E -s` and deletes any old reward files first.
- **No general internet during the agent phase.** `[agent].network_mode = "allowlist"`: like the original (a plain model API call), the agent cannot browse. Harbor adds the model endpoint for agents that call their model from inside the sandbox; agents that call the model from the host (for example `terminus-2` and `bfcl-fc-agent`) need no sandbox egress. Use `--allow-agent-host` if your agent needs another host.
- **`bfcl-fc-agent` (parity agent).** `bfcl_autoadapter.agents.bfcl_fc_agent:BfclFcAgent` reproduces the original BFCL harness for OpenAI-compatible models in function-calling (FC) mode (`OpenAICompletionsHandler`, `inference_single_turn_FC`): it sends the conversation as messages and the hinted function docs as tools (converted with BFCL's `convert_to_tool`, which replaces `.` in function names with `_`), makes one `chat.completions` call (temperature 0.001, BFCL's default; rate-limit retries with BFCL's backoff), decodes the tool calls like BFCL's `decode_ast`, maps the function names back to the task's names (a name that was not among the tools sent gets the prefix `unknown_tool:` so that it never matches, as upstream), and writes the result to `/app/result.json`. It runs on the host and reads `OPENAI_API_KEY` / `OPENAI_BASE_URL`.

## Generated Task Structure

```
datasets/bfcl-autoadapter/
├── bfcl-autoadapter__simple-python-0/
│   ├── task.toml              # name = "gorilla-llm/bfcl-autoadapter__simple-python-0", BFCL id and category in [metadata]
│   ├── instruction.md         # BFCL prompt + answer format + conversation (JSON) + function docs (JSON)
│   ├── environment/
│   │   └── Dockerfile         # python:3.12-slim-bookworm, identical for all tasks
│   ├── solution/
│   │   ├── solve.sh           # copies result.json to /app/result.json
│   │   └── result.json        # oracle answer
│   └── tests/
│       ├── test.sh            # runs evaluate.py, writes /logs/verifier/reward.txt
│       ├── evaluate.py        # BFCL AST / relevance grading of /app/result.json
│       ├── task_spec.json     # BFCL id, category, function docs (as the checker uses them), ground truth
│       └── bfcl_checker/      # vendored BFCL checker code
├── bfcl-autoadapter__live-simple-0-0-0/
└── ...
```

Task folder and task name: `bfcl-autoadapter__<BFCL id>`, lowercased, with `_` replaced by `-` (for example `live_multiple_10-4-2` → `bfcl-autoadapter__live-multiple-10-4-2`).

The answer file is a JSON array with one object per call:

```json
[{"calculate_triangle_area": {"base": 10, "height": 5}}]
```

`[]` means "no function call". Reward is 1 if BFCL's checker accepts the answer and 0 otherwise.

## Run Evaluation / Harness

### Running with Datasets Registry

```bash
# Oracle
harbor run -d gorilla-llm/bfcl-autoadapter -a oracle

# Any Harbor agent and model
harbor run -d gorilla-llm/bfcl-autoadapter -a <agent> -m "<model>"

# The BFCL function-calling agent (needs this adapter package installed in Harbor's environment)
uvx --python 3.12 --from "harbor[daytona]==0.25.0" --with ./src/bfcl-autoadapter \
  harbor run -d gorilla-llm/bfcl-autoadapter \
  -a bfcl_autoadapter.agents.bfcl_fc_agent:BfclFcAgent -m openai/deepseek-v4.1-flash
```

### Using Job Configurations

```bash
# From the repository root. Default config: oracle on the full local dataset (Docker).
harbor run -c src/bfcl-autoadapter/run_bfcl-autoadapter.yaml

# Or point at the local dataset directly
harbor run -p datasets/bfcl-autoadapter -a <agent> -m "<model>"

# On Daytona, reuse one snapshot for all tasks
harbor run -p datasets/bfcl-autoadapter -a <agent> -m "<model>" -e daytona --ek auto_snapshot=true -n 100
```

Uncomment the agent block you want in `run_bfcl-autoadapter.yaml` (oracle by default; the BFCL FC agent and a standard CLI agent are listed).

### Running Individual Trial

```bash
harbor trial start -p datasets/bfcl-autoadapter/bfcl-autoadapter__simple-python-0 -a oracle
harbor trial start -p datasets/bfcl-autoadapter/bfcl-autoadapter__simple-python-0 -a <agent> -m "<model>"
```

## Usage: Create Task Directories

```bash
cd src/bfcl-autoadapter
uv sync

# All 3635 tasks (fetches BFCL at the pinned commit)
uv run bfcl-autoadapter --output-dir ../../datasets/bfcl-autoadapter

# Use an existing checkout of ShishirPatil/gorilla instead of fetching
uv run bfcl-autoadapter --output-dir ../../datasets/bfcl-autoadapter --bfcl-root /path/to/gorilla

# Only some categories, some tasks, or the first N tasks
uv run bfcl-autoadapter --output-dir ../../datasets/bfcl-autoadapter --categories non_live live_simple
uv run bfcl-autoadapter --output-dir ../../datasets/bfcl-autoadapter --task-ids simple_python_0 live_multiple_10-4-2
uv run bfcl-autoadapter --output-dir ../../datasets/bfcl-autoadapter --limit 20 --overwrite
```

Flags: `--output-dir`, `--limit`, `--overwrite`, `--task-ids` (BFCL ids or task folder names), `--categories`, `--bfcl-root`. The task names do not depend on the flags, so they stay stable between runs.

## Comparison with Original Benchmark (Parity)

**Oracle.** The oracle passes all 3635 tasks (3635/3635, mean reward 1.000, 0 errors) with Harbor 0.25.0 on Daytona.

**Setup.** BFCL has no agent framework: its harness sends each entry straight to the model API. The parity agent `bfcl-fc-agent` (see [Adapter Features](#adapter-features)) sends the same request from Harbor, so both sides measure the same model in BFCL's function-calling mode. Both sides used `deepseek-v4.1-flash` through the same OpenAI-compatible endpoint (served as `deepseek-v4-1-flash-261017`), temperature 0.001, all 3635 tasks, 3 runs per side, with the two sides running at the same time. Metric: accuracy in percent (share of tasks that BFCL's checker accepts), mean ± sample SEM over the runs.

| Agent | Model | Metric | Number of Runs | Dataset Size | Original Benchmark Performance | Harbor Adapter Performance |
|-------|-------|--------|----------------|--------------|--------------------------------|----------------------------|
| bfcl-fc-agent@1.0.0 | deepseek-v4.1-flash | Accuracy (%) | 3 | 3635 (100%) | 78.12 ± 0.09 | 78.21 ± 0.02 |

Runs: original 77.96, 78.13, 78.27; Harbor 78.18, 78.24, 78.21. The ranges overlap. No run had an inference error or a trial exception. Concurrency: 100 per side in run 1 (`-n 100` / 100 threads); runs 2 and 3 ran at the same time with 50 each.

The request that `bfcl-fc-agent` sends was compared with the request built by BFCL's own handler code: identical for all 3635 tasks. Grading the raw model responses of each side with the other side's decoding and checker gives the same verdicts (0 differences in 2 × 3635 cases). The per-task differences between runs come from the model answering the same request differently.

**Standard CLI agent.** `terminus-2@2.0.0` with `deepseek-v4.1-flash` on a 107-task sample (all 13 categories in proportion, at least 2 per category, drawn with `random.Random(20261107)`; the ID list is `harbor_parity/cli-check-terminus2/cli_check_task_ids.json` in the parity results): 93/107 = 86.9 %, 0 errors. `bfcl-fc-agent` scored 86, 85 and 81 of the same 107 tasks in the three parity runs.

**Reproduce.** Original side: the fork [MrLYG/gorilla, branch `harbor-parity`](https://github.com/MrLYG/gorilla/tree/harbor-parity) adds only the model entry and the scripts in `berkeley-function-call-leaderboard/harbor_parity/` (see its README).

```bash
cd gorilla/berkeley-function-call-leaderboard            # branch harbor-parity
pip install -e . && pip install soundfile
export OPENAI_API_KEY=... OPENAI_BASE_URL=...            # endpoint serving deepseek-v4.1-flash
harbor_parity/run_parity.sh run1 100                     # writes harbor_parity/runs/run1/summary.json
harbor_parity/run_parity.sh run2 50 &                    # runs 2 and 3 at the same time
harbor_parity/run_parity.sh run3 50
```

Harbor side (from the repository root):

```bash
(cd src/bfcl-autoadapter && uv run bfcl-autoadapter --output-dir ../../datasets/bfcl-autoadapter)
for r in 1 2 3; do
  uvx --python 3.12 --from "harbor[daytona]==0.25.0" --with ./src/bfcl-autoadapter harbor run \
    -p datasets/bfcl-autoadapter -e daytona --ek auto_snapshot=true -n 100 \
    -a bfcl_autoadapter.agents.bfcl_fc_agent:BfclFcAgent -m openai/deepseek-v4.1-flash \
    --job-name parity-harbor-run$r
done
```

Use a new run name for every original-side run (`run_parity.sh` refuses an existing one, so each run makes fresh model calls). The accuracy of a Harbor run is the job's mean reward × 100. Standard CLI agent check: `harbor run -p datasets/bfcl-autoadapter -a terminus-2 -m openai/deepseek-v4.1-flash -e daytona --ek auto_snapshot=true` with one `-i bfcl-autoadapter__<id>` filter per ID in `cli_check_task_ids.json` (ID lowercased, `_` → `-`).

**Links.** Original benchmark: [ShishirPatil/gorilla › berkeley-function-call-leaderboard](https://github.com/ShishirPatil/gorilla/tree/main/berkeley-function-call-leaderboard). Original-side fork: [MrLYG/gorilla@harbor-parity](https://github.com/MrLYG/gorilla/tree/harbor-parity) (commit [`ad0f5cc`](https://github.com/MrLYG/gorilla/commit/ad0f5cc083a4c12480c7ebc2c76410e0e74e60e2)). Adapter PR: [harbor-index/adapter-automation#7](https://github.com/harbor-index/adapter-automation/pull/7). Dataset PR: [harbor-framework/harbor-datasets#275](https://github.com/harbor-framework/harbor-datasets/pull/275). Parity results: [harborframework/parity-experiments#279](https://huggingface.co/datasets/harborframework/parity-experiments/discussions/279).

## Notes & Caveats

- **Excluded entries (6).** No answer can pass these with the original checker, so every model scores 0 on them upstream. They are left out; the ground truth is not changed.
  - `simple_java_36`, `simple_java_65`: the parameter is an `ArrayList` of `any`; the Java converter turns every element into a string, but the ground truth expects the int `42` / the boolean `true`.
  - `live_simple_106-63-0`, `live_simple_112-68-0`: parameters that the function doc marks as required have an empty list of accepted values.
  - `live_multiple_862-181-3`: the ground truth requires `journey_start_time`, which is not a parameter of `Trains_1_FindTrains`.
  - `live_multiple_964-207-0`: the function doc requires `statement`, but the ground truth only accepts `question`.
- **Upstream quirk kept as is.** In `simple_java_64` the accepted value of the `meta` map key `format` is a plain string instead of a list. The BFCL checker then rejects any value for that key and accepts the map only without it (for example `new HashMap<>()`). The oracle uses that answer; agents that pass `format` fail, as they do upstream.
- **Answer format.** Upstream, the harness decodes the model's API response. Here the agent writes the decoded form to `/app/result.json`. A missing or invalid file counts like an undecodable response: incorrect for the call categories and correct for the irrelevance categories (no call was made). This matches BFCL, where an agent that makes no call also passes `irrelevance` / `live_irrelevance`.
- **Instruction.** BFCL's FC mode sends only the conversation and the tools. For text-based agents the instruction adds BFCL's default prompt (the "classic" system prompt BFCL uses for prompting-mode models) and the answer-file format. The `bfcl-fc-agent` ignores this text and sends exactly what BFCL sends.
- **Function names with dots.** OpenAI-style tool names cannot contain `.`, so BFCL renames `math.factorial` to `math_factorial` for FC models and compares with the renamed ground truth (`underscore_to_dot=True`). The Harbor verifier compares with the names as written in the task, and `bfcl-fc-agent` maps the renamed calls back before writing the answer. The result is the same.
- **Metric.** The Harbor score is the mean reward over tasks, i.e. the fraction of tasks answered correctly (micro average). The BFCL leaderboard reports accuracy per category and summary columns that average the categories (unweighted for non-live, weighted by category size for live), so the Harbor mean is not directly comparable with those summary columns. Per-category accuracies can be recomputed from the trial results (`[metadata].bfcl_test_category`).
- **Relevance oracle.** BFCL has no ground truth for `live_relevance`; its check only requires at least one function call. The oracle calls the first listed function with no arguments.
- **Public answers.** BFCL's data and ground truth are public (GitHub, Hugging Face). The instruction template names no benchmark, dataset or source URL. A few original requests contain such text themselves. (a) Six name the Gorilla project: `live_simple_78-39-0`, `live_simple_79-40-0` and `live_irrelevance_91-2-79` have a system message "utilizing the Gorilla LLM model"; `live_irrelevance_547-169-0` has an earlier assistant turn "as an AI developed by Gorilla LLM"; `live_simple_1-1-0` asks for the star history of `ShishirPatil/gorilla`; the function doc of `live_irrelevance_850-340-0` lists the model enum value `GORILLA_OPENFUNCTIONS_V2`. (b) Five contain GitHub or Hugging Face URLs that are the object of the request, not the source of the task: `simple_javascript_45` (`github.com/yarnpkg/berry`), `live_irrelevance_255-54-0`, `live_irrelevance_256-55-0` and `live_parallel_multiple_8-7-0` (a `zelarhq/nodejs-welcome` repository to clone), `live_simple_146-95-3` (`huggingface.co`). These texts are kept unchanged on purpose: the adapter does not change the benchmark's prompts, and both parity sides use the same unchanged inputs. This is a deliberate, documented exception to keeping the source anonymous; it applies only to text that is part of the original requests. The agent-phase network allowlist keeps agents from looking answers up; do not widen it beyond the model endpoint, and do not give the agent web-search tools.
- **Harness settings.** Each task gives the agent 600 s (`[agent].timeout_sec`). BFCL itself has no overall time limit and retries rate-limit errors without end; `bfcl-fc-agent` uses the same retry rule but stops at the task's time limit. In the parity runs no limit came close: the longest single model call took 44.8 s on the Harbor side (44.7 s on the original side) and the longest agent phase took 47.9 s; no call ended in an error. `bfcl-fc-agent` reads only `OPENAI_API_KEY` and `OPENAI_BASE_URL`; BFCL's OpenAI handler also reads `OPENAI_DEFAULT_HEADERS` (extra HTTP headers). That variable was not set on either side in the parity runs and no `.env` file was used, so both sides sent the client's default headers. The agent does not support endpoints that need extra headers.
- **Vendored code.** The files in `tests/bfcl_checker/` are kept byte-for-byte equal to upstream apart from import lines, so that grading matches the original exactly; `bfcl_upstream.py` (the language hints and the tool conversion) keeps the upstream logic, reduced to the branches used here. These copies are exempt from code-style cleanup on purpose: they keep upstream style such as the unused `parallel_function_checker_enforce_order` in `ast_checker.py` and a bare `except` in `js_type_converter.py`. The exemption covers only these copied files.
- **Original-side setup.** Installing `bfcl_eval` at the pinned commit with `pip install -e .` leaves out `soundfile`, which `qwen-agent` imports; install it separately (`pip install soundfile`).

## Installation / Prerequisites

- Harbor 0.25.0 (`uv tool install harbor==0.25.0`, or `uvx --from "harbor[daytona]==0.25.0" harbor ...`).
- Docker, or a Daytona account (`DAYTONA_API_KEY`) for `-e daytona`.
- Python 3.12+ and [uv](https://docs.astral.sh/uv/). From `src/bfcl-autoadapter/`: `uv sync` installs the adapter (the dev group pins Harbor for the custom agent).
- `git`, to fetch the BFCL data (or pass `--bfcl-root`).
- Model API keys for the agent you run. `bfcl-fc-agent` needs `OPENAI_API_KEY` and, for a non-OpenAI endpoint, `OPENAI_BASE_URL`.

## Troubleshooting

- **`bfcl_autoadapter` not found when using `bfcl-fc-agent`:** Harbor must import the adapter package. Add `--with ./src/bfcl-autoadapter` to the `uvx` command, or install the package into Harbor's environment.
- **Many sandboxes are slow to start on Daytona:** pass `--ek auto_snapshot=true`; all tasks share one environment, so one snapshot is built and reused.
- **Reward 0 with "does not exist" in `verifier/details.json`:** the agent did not write `/app/result.json`. For irrelevance tasks this still counts as correct (no call).
- **Reward 0 on a Java/JavaScript task with a type error:** these parameters take a string in Java/JavaScript syntax (for example `"new ArrayList<>(Arrays.asList(1, 2))"` or `"[1, 2]"`), as the parameter descriptions say.
- **`verifier/details.json`** holds the BFCL checker's error message and error type for every failed task.

## Citation

```bibtex
@inproceedings{patil2025bfcl,
  title={The Berkeley Function Calling Leaderboard ({BFCL}): From Tool Use to Agentic Evaluation of Large Language Models},
  author={Patil, Shishir G. and Mao, Huanzhi and Yan, Fanjia and Ji, Charlie Cheng-Jie and Suresh, Vishnu and Stoica, Ion and Gonzalez, Joseph E.},
  booktitle={Forty-second International Conference on Machine Learning},
  year={2025},
  url={https://openreview.net/forum?id=2GmDdhBdDk}
}
```

## Authors & Contributions

This adapter is developed and maintained by [Yuangang Li](mailto:liyuangang66@gmail.com).

**Issues and Contributions:**
- Submit issues and pull requests to the adapters repository.
- Follow the project's coding style and commit guidelines.
