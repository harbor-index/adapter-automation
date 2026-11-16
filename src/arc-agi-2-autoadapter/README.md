## ARC-AGI-2 → Harbor Adapter

## Overview

This adapter converts the public evaluation set of [ARC-AGI-2](https://github.com/arcprize/ARC-AGI-2) into Harbor tasks.

- **Task type**: abstract reasoning on colored grids. Each ARC task shows 2 to 6 demonstration input/output grid pairs; the solver must produce the output grid for a new test input. Grids are 1x1 to 30x30, cells are integers 0-9.
- **Source**: [arcprize/ARC-AGI-2](https://github.com/arcprize/ARC-AGI-2) (Apache-2.0), pinned at commit `f3283f727488ad98fe575ea6a5ac981e4a188e49`. Scoring follows the official ARC Prize harness [arcprize/arc-agi-benchmarking](https://github.com/arcprize/arc-agi-benchmarking).
- **Size**: **167 Harbor tasks** = the 167 test inputs of the 120 public evaluation tasks (75 tasks have 1 test input, 43 have 2, 2 have 3). No task is excluded. The 1,000 training tasks (1,076 test inputs) can be generated with `--split training`; they are not part of the registered dataset. The semi-private and private ARC-AGI-2 sets are not public.
- **Unit of a Harbor task**: one test input of one ARC task, because the official harness prompts and checks each test input separately. Task names are `arcprize/arc-agi-2-autoadapter__<arc_task_id>-<test_index>` (for example `arcprize/arc-agi-2-autoadapter__0934a4d8-0`).
- **Main adaptation**: the instruction is the official harness prompt, followed by one paragraph that says to write the answer grid to `/app/output.json`. The verifier checks that file by exact match. Nothing else is changed.

## What is ARC-AGI-2?

ARC-AGI-2 ("Abstraction and Reasoning Corpus for Artificial General Intelligence, v2") is a benchmark of few-shot grid puzzles by the ARC Prize Foundation ([paper](https://arxiv.org/abs/2505.11831), [website](https://arcprize.org/arc-agi/2/)). Every task needs a new transformation rule that the solver must infer from a few examples, using only "core knowledge" priors (objects, counting, geometry, and so on). Every public evaluation task was solved by at least 2 people in at most 2 attempts; the average human test-taker solved 66%.

Scoring (repository README and the harness `scoring/scoring.py`): the output grid must match exactly, including its size. Each test input gets 2 attempts and counts as solved if either attempt is correct. A task scores the fraction of its test inputs that are solved, and the benchmark score is the mean task score.

## Adapter Features

- **Pinned data download**: downloads the ARC-AGI-2 repository at a fixed commit from GitHub (or reads a local copy with `--data-dir`), validates every grid, and writes one task per test input. Task names are stable across runs.
- **Same prompt as the official harness**: `instruction.md` is built exactly like `convert_task_pairs_to_prompt` in arc-agi-benchmarking (same template text, same `json.dumps` grid format, all demonstration pairs and one test input), followed by the output paragraph.
- **Environment**: one shared image for all tasks (`python:3.12-slim` with `numpy`, working directory `/app`). All task data is in the instruction, as in the official prompt.
- **Exact-match verifier in a separate container**: `tests/verify.py` loads `/app/output.json` and compares it with the expected grid using the same equality as the official scorer (`answer == expected`). Reward is 1 or 0. A missing, invalid or non-regular (for example symlinked) file gives 0. Details are written to `/logs/verifier/details.json`. The verifier runs in a fresh copy of the environment (`[verifier] environment_mode = "separate"`); `/app/output.json` is the only artifact copied from the agent's sandbox, so the agent cannot change the grader or the reward files.
- **Oracle**: `solution/solve.sh` writes the expected grid; it scores 1 on every task.
- **Official score helper**: `arc-agi-2-autoadapter-score` turns a Harbor job run with `-k 2` into the official ARC-AGI-2 score (2 attempts per test input, averaged per ARC task). Harbor's own mean reward is the per-attempt accuracy over test inputs, which is a different number.
- **Parity agent** (`arc_agi_2_autoadapter.parity_agent:ArcHarnessAgent`, `arc-harness-chat@1.0.0`): reproduces one attempt of the official harness with an OpenAI-compatible model: it sends the instruction as one user message to the Chat Completions API, parses the reply with the harness parser (copied from `utils/parsing.py`: `\boxed{}` first, then the last JSON list of lists), validates it as `List[List[int]]`, retries once on an API error or parse failure (harness `--retry_attempts 2`), and writes the grid to `/app/output.json`. The model call runs on the Harbor host. Options (`--ak`): `max_tokens`, `temperature`, `stream`, `retry_attempts`, `request_timeout_sec`.

## Generated Task Structure

```
datasets/arc-agi-2-autoadapter/
├── 0934a4d8-0/                   # <arc_task_id>-<test_index>
│   ├── task.toml                 # name, authors, ARC metadata, timeouts, resources,
│   │                             # artifacts = ["/app/output.json"], separate verifier
│   ├── instruction.md            # official harness prompt + output paragraph
│   ├── environment/
│   │   └── Dockerfile            # python:3.12-slim + numpy (same for every task)
│   ├── solution/
│   │   └── solve.sh              # oracle: writes the expected grid to /app/output.json
│   └── tests/
│       ├── test.sh               # runs verify.py, writes /logs/verifier/reward.txt
│       ├── verify.py             # exact-match check
│       └── expected_output.json  # expected grid (only visible to the verifier)
├── 135a2760-0/
└── ...
```

Adapter code:

```
src/arc-agi-2-autoadapter/
├── README.md
├── adapter_metadata.json
├── parity_experiment.json
├── pyproject.toml
├── uv.lock
├── run_arc-agi-2-autoadapter.yaml       # reference job config (oracle by default)
├── run_arc-agi-2-autoadapter_parity.yaml # parity job config (parity agent)
└── src/arc_agi_2_autoadapter/
    ├── __init__.py
    ├── adapter.py           # download, prompt construction, task generation
    ├── main.py              # CLI: uv run arc-agi-2-autoadapter
    ├── score.py             # CLI: uv run arc-agi-2-autoadapter-score (official score)
    ├── parity_agent.py      # parity agent (official harness call as a Harbor agent)
    ├── prompts/
    │   ├── system_prompt.txt                # verbatim copy of the harness prompt template
    │   └── LICENSE-arc-agi-benchmarking.md  # license of the copied harness code
    └── task-template/
        ├── task.toml
        ├── instruction.md
        ├── environment/Dockerfile
        ├── solution/solve.sh
        └── tests/{test.sh,verify.py}
```

## Run Evaluation / Harness

### Running with the registry

After the dataset is published:

```bash
# Oracle (reward 1 on every task)
harbor run -d arcprize/arc-agi-2-autoadapter

# Any agent; use -k 2 for the two ARC-AGI-2 attempts per test input
harbor run -d arcprize/arc-agi-2-autoadapter -a <agent_name> -m "<model_name>" -k 2
```

### Running with locally generated tasks

From the repository root, after generating the tasks (see [Usage](#usage-create-task-directories)):

```bash
# Oracle
harbor run -p datasets/arc-agi-2-autoadapter -a oracle

# An agent, 2 attempts per test input, on Daytona
harbor run -p datasets/arc-agi-2-autoadapter -a <agent_name> -m "<model_name>" -k 2 -e daytona -n 32

# Reference config (oracle by default; edit the agents block for other agents)
harbor run -c src/arc-agi-2-autoadapter/run_arc-agi-2-autoadapter.yaml

# One task
harbor trial start -p datasets/arc-agi-2-autoadapter/0934a4d8-0 -a <agent_name> -m "<model_name>"
```

### Official ARC-AGI-2 score

Harbor reports the mean reward over trials (accuracy of single attempts). The official score gives each test input 2 attempts and averages per ARC task. Run with `-k 2`, then:

```bash
cd src/arc-agi-2-autoadapter
uv run arc-agi-2-autoadapter-score ../../jobs/<job-name> --dataset-dir ../../datasets/arc-agi-2-autoadapter
```

## Usage: Create Task Directories

```bash
cd src/arc-agi-2-autoadapter
uv sync

# Public evaluation set (default): 167 tasks into <repo>/datasets/arc-agi-2-autoadapter
uv run arc-agi-2-autoadapter --output-dir ../../datasets/arc-agi-2-autoadapter

# Options
uv run arc-agi-2-autoadapter --task-ids 0934a4d8 135a2760-0   # an ARC task (all its test inputs) or one test input
uv run arc-agi-2-autoadapter --limit 10 --overwrite
uv run arc-agi-2-autoadapter --split training --output-dir /tmp/arc-agi-2-training   # 1,076 tasks, not registered
uv run arc-agi-2-autoadapter --data-dir /path/to/ARC-AGI-2/data                       # use a local copy
```

Flags: `--output-dir`, `--limit`, `--overwrite`, `--task-ids`, `--split {evaluation,training,all}` (default `evaluation`), `--data-dir`.

## Comparison with Original Benchmark (Parity)

**Oracle**: the oracle agent scores 1.0 on all 167 tasks (Harbor `0.25.0`, Daytona), i.e. 100% with the official scorer.

**Setup**. The original side is the official ARC Prize harness [arcprize/arc-agi-benchmarking](https://github.com/arcprize/arc-agi-benchmarking) at commit `54629ad8`, with two changes on the fork branch [`harbor-parity`](https://github.com/MrLYG/arc-agi-benchmarking/tree/harbor-parity): the output paragraph is appended to the prompt (the same text as in `instruction.md`), and a model config for the parity model is added. The Harbor side runs the parity agent `arc-harness-chat@1.0.0` (see [Adapter Features](#adapter-features)), which makes the same call as the harness. Both sides: model `deepseek-v4.1-flash` through the same OpenAI-compatible gateway (the gateway reports the dated model ID `deepseek-v4-1-flash-261017`; checked before and after the runs, `served_model_check.json` in the parity upload), Chat Completions with streaming, `max_tokens=131072`, no temperature set, 2 attempts per test input, 2 calls at most per attempt. The prompts were checked to be byte-identical on all 167 test inputs.

**Metric**: the official ARC-AGI-2 score in percent (2 attempts per test input, task score = solved test inputs / test inputs, mean over ARC tasks), computed by the harness's `scoring.py` on the original side and by `arc-agi-2-autoadapter-score` on the Harbor side.

| Agent | Model | Metric | Number of Runs | Dataset Size | Original Benchmark Performance | Harbor Adapter Performance |
|-------|-------|--------|----------------|--------------|--------------------------------|----------------------------|
| arc-harness-chat@1.0.0 | deepseek-v4.1-flash | ARC-AGI-2 score (pass@2, %) | 3 | 167 tasks (100%; 120 ARC tasks) | 77.08 ± 1.32 | 77.59 ± 0.67 |

Scores are mean ± sample SEM over runs. Individual runs: original 79.44, 76.94, 74.86; Harbor 78.19, 76.25, 78.33. The run ranges overlap. In each run, each side made 334 attempts (167 test inputs x 2). Attempts that needed the second call (empty or unparsable reply, usually at the 131072-token limit): original 2/0/3, Harbor 2/3/4 in runs 1/2/3; attempts left without an answer: original 0/0/1, Harbor 0/0/1. There were no Harbor trial exceptions. Per-task comparison of run 1: the original side was better on 11 ARC tasks, Harbor on 9, and they were equal on 100. Full results: `parity_experiment.json`; all run files (submissions, logs, Harbor jobs, oracle job) are in the [parity results upload](https://huggingface.co/datasets/harborframework/parity-experiments/discussions/280).

**Links**: [original benchmark](https://github.com/arcprize/ARC-AGI-2), [official harness](https://github.com/arcprize/arc-agi-benchmarking), [original-side fork](https://github.com/MrLYG/arc-agi-benchmarking/tree/harbor-parity), [adapter PR](https://github.com/harbor-index/adapter-automation/pull/9), [dataset PR](https://github.com/harbor-framework/harbor-datasets/pull/276), [parity results](https://huggingface.co/datasets/harborframework/parity-experiments/discussions/280).

**Standard CLI agent check (Harbor only)**: `claude-code@2.1.311` with the same model (`deepseek-v4.1-flash` through the gateway's Anthropic-compatible endpoint), all 167 tasks, 1 attempt per test input (`-k 1`), `CLAUDE_CODE_MAX_OUTPUT_TOKENS=131072`: mean reward 0.731 (122 of 167 correct; 6 trials reached the 3600 s agent timeout and scored 0), ARC-AGI-2 score with 1 attempt 71.81%. This shows that a standard CLI agent can read the instruction, work in the environment and write `/app/output.json`. A first run with Claude Code's default 32K output cap scored lower (mean reward 0.431), because 71 trials stopped with `OutputTokenExceededError` before writing an answer.

```bash
export ANTHROPIC_API_KEY=...  ANTHROPIC_BASE_URL=<gateway>   # Anthropic-compatible endpoint serving the model
harbor run -p datasets/arc-agi-2-autoadapter -a claude-code -m deepseek-v4.1-flash --ak version=2.1.311 \
  --ae CLAUDE_CODE_MAX_OUTPUT_TOKENS=131072 --ae ANTHROPIC_DEFAULT_HAIKU_MODEL=deepseek-v4.1-flash \
  --ae ANTHROPIC_DEFAULT_SONNET_MODEL=deepseek-v4.1-flash --ae ANTHROPIC_DEFAULT_OPUS_MODEL=deepseek-v4.1-flash \
  --ae CLAUDE_CODE_SUBAGENT_MODEL=deepseek-v4.1-flash -k 1 -e daytona -n 60
```

**Reproduce the original side** (fork branch `harbor-parity`):

```bash
git clone -b harbor-parity https://github.com/MrLYG/arc-agi-benchmarking.git arc-agi-benchmarking
cd arc-agi-benchmarking
git checkout ccbedeaa4f6e472888376a103ca49cec63f0c860   # code used for the runs (later commits change only the README)
uv sync --python 3.12
git clone https://github.com/arcprize/ARC-AGI-2.git data/arc-agi
git -C data/arc-agi checkout f3283f727488ad98fe575ea6a5ac981e4a188e49
export OPENAI_API_KEY=...            # and OPENAI_BASE_URL=<gateway>/v1
uv run cli/run_all.py --config deepseek-v4.1-flash-harbor-parity \
  --data_dir data/arc-agi/data/evaluation \
  --save_submission_dir submissions/run1 --logs-base-dir logs/run1 \
  --num_attempts 2 --retry_attempts 2
uv run src/arc_agi_benchmarking/scoring/scoring.py \
  --task_dir data/arc-agi/data/evaluation --submission_dir submissions/run1 --results_dir results/run1
```

`run_all.py` runs at most 32 tasks at a time (Python's default thread pool). For runs 2 and 3 we started 4 `run_all.py` processes per run with disjoint `--task_list_file` lists of 30 tasks (same `--save_submission_dir`), so that all 120 tasks ran at once. This changes only the wall time, not the results.

**Reproduce the Harbor side** (from the repository root):

```bash
(cd src/arc-agi-2-autoadapter && uv run arc-agi-2-autoadapter --output-dir ../../datasets/arc-agi-2-autoadapter)
export OPENAI_API_KEY=...            # and OPENAI_BASE_URL=<gateway>/v1
PYTHONPATH=src/arc-agi-2-autoadapter/src harbor run \
  -c src/arc-agi-2-autoadapter/run_arc-agi-2-autoadapter_parity.yaml --job-name parity-harbor-run1
(cd src/arc-agi-2-autoadapter && uv run arc-agi-2-autoadapter-score ../../jobs/parity-harbor-run1 \
  --dataset-dir ../../datasets/arc-agi-2-autoadapter)
```

## Notes & Caveats

- **Output paragraph (prompt change, applied on both sides)**: agents need to know where to put the answer, so the instruction appends one paragraph after the official prompt: write the grid as JSON to `/app/output.json`. For parity, the original harness used the same paragraph (fork branch `harbor-parity`), so both sides sent byte-identical prompts. This changes the prompt the model sees compared with an unmodified harness run.
- **One Harbor task per test input**: the 45 ARC tasks with 2 or 3 test inputs become 2 or 3 Harbor tasks. Harbor's mean reward therefore weights test inputs, not ARC tasks; use `arc-agi-2-autoadapter-score` for the official per-task average.
- **Two attempts**: the official score allows 2 attempts per test input. In Harbor these are 2 independent trials (`-k 2`), like the harness's 2 independent calls. With `-k 1` the score helper reports a 1-attempt score. Harbor's own summary table may also show a `Pass@2` column: that is pass@2 averaged over the 167 test inputs, not the official per-ARC-task average.
- **Answer format**: the verifier reads `/app/output.json` as JSON and compares it with Python equality, like the official scorer compares parsed answers. Integral floats such as `1.0` compare equal to `1`; strings, a wrapping object, or a different size score 0. The harness's text parser is used only by the parity agent, because CLI agents write the file themselves.
- **Data version**: the tasks come from upstream commit `f3283f7` (includes all fixes listed in the upstream `changelog.md` up to 2025-05-15).
- **Model calls of the parity agent** run on the Harbor host, not in the sandbox; the sandbox only receives the answer file and runs the verifier.

## Installation / Prerequisites

- Python 3.11+ and [uv](https://docs.astral.sh/uv/).
- Harbor CLI (`uv tool install harbor`); this adapter was validated with Harbor `0.25.0`.
- Docker, or a Daytona account (`DAYTONA_API_KEY`) for `-e daytona`.
- Network access to GitHub when generating tasks (the pinned data tarball is downloaded), unless `--data-dir` is used.
- For the parity agent: `OPENAI_API_KEY` (and `OPENAI_BASE_URL` for an OpenAI-compatible gateway), and `PYTHONPATH=src/arc-agi-2-autoadapter/src` so Harbor can import the agent.

## Troubleshooting

- **All rewards are 0 for an agent**: check `/logs/verifier/details.json` in the trial folder. `missing /app/output.json` means the agent did not write the file; `incorrect` with a different `answer_shape` usually means a wrong grid size.
- **Reasoning models return empty answers**: reasoning tokens count toward `max_tokens`. With a small limit the model stops before it writes the grid. The parity runs used `max_tokens=131072`.
- **Claude Code fails with `OutputTokenExceededError`**: with a reasoning model, one response can exceed Claude Code's default output cap (32K tokens). Raise it, for example `--ae CLAUDE_CODE_MAX_OUTPUT_TOKENS=131072`.
- **`ModuleNotFoundError: arc_agi_2_autoadapter`** when running the parity agent: set `PYTHONPATH=src/arc-agi-2-autoadapter/src` (from the repository root).

## Citation

```bibtex
@article{chollet2025arcagi2,
  title   = {ARC-AGI-2: A New Challenge for Frontier AI Reasoning Systems},
  author  = {Chollet, Francois and Knoop, Mike and Kamradt, Gregory and Landers, Bryan and Pinkard, Henry},
  journal = {arXiv preprint arXiv:2505.11831},
  year    = {2025},
  url     = {https://arxiv.org/abs/2505.11831}
}
```

## Authors & Contributions

This adapter is developed and maintained by Yuangang Li (liyuangang66@gmail.com).

- Benchmark: Francois Chollet, Mike Knoop, Gregory Kamradt, Bryan Landers, Henry Pinkard (ARC Prize Foundation).
- Issues and pull requests: [harbor-index/adapter-automation](https://github.com/harbor-index/adapter-automation).

## Acknowledgement

The prompt template (`prompts/system_prompt.txt`) and the answer parser of the parity agent are copied from [arcprize/arc-agi-benchmarking](https://github.com/arcprize/arc-agi-benchmarking) (MIT License, Copyright (c) 2025 ARC Prize Foundation; the license text is in `src/arc_agi_2_autoadapter/prompts/LICENSE-arc-agi-benchmarking.md`). The task data comes from [arcprize/ARC-AGI-2](https://github.com/arcprize/ARC-AGI-2) (Apache-2.0).
