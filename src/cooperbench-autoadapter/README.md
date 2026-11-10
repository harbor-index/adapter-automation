# CooperBench → Harbor Adapter

## Overview

This adapter converts [CooperBench](https://github.com/cooperbench/CooperBench) into Harbor tasks.
CooperBench measures whether two coding agents can cooperate: each agent implements one feature of
the same real pull request in its own copy of the repository, they can only talk through messages,
and the pair is solved only if the hidden tests of **both** features pass on the merge of their two
patches.

- Source: [cooperbench/CooperBench](https://github.com/cooperbench/CooperBench) at commit
  `63b9d44d9f39a02fccf5bf0052db48a917a011fd` (dataset, evaluator and agent code).
- Size: **652 Harbor tasks**, one per feature pair: 30 pull-request tasks in 12 repositories
  (Python, Go, Rust, TypeScript), 199 features, every 2-combination of a task's features.
  This is the benchmark's full pair set (`dataset/subsets/all.json`); nothing is excluded.
- Setting: `coop` (two agents, messaging on, shared git off), the benchmark's default.
- Metric: pass rate over pairs (reward 1 when both features' tests pass, else 0).
- Main adaptation: the original harness runs two host-side agent loops that `docker exec` into
  two containers and talk through Redis. Here each Harbor trial is a three-container compose
  environment, and a custom Harbor agent runs the original mini-swe-agent v2 coop loops against it.

## What is CooperBench?

CooperBench (Khatua, Zhu et al., 2026, [arXiv:2601.13295](https://arxiv.org/abs/2601.13295),
[cooperbench.com](https://cooperbench.com)) asks "can coding agents be your teammates?". Every
task is a real merged pull request split into several features that touch overlapping code. Two
agents receive one feature each, work in separate sandboxes, coordinate through a mailbox
(`send_message`), and each submits a pull request to a shared local remote. The evaluator merges
the two submissions with plain `git merge` and runs both features' hidden tests on the result;
when the merge conflicts, it falls back to the first agent's patch alone against both test suites.
The benchmark reports the fraction of pairs where both features pass.

## Adapter Features

- **One task per feature pair**, enumerated exactly like the original `discover_tasks`.
  `--split` selects the full set (default), an upstream pair subset (`lite`, `flash`, `core`,
  `flash_10`) or the adapter's `parity` split. The upstream `team3_*` subsets list three-agent
  groups, which this two-agent adapter does not generate; asking for them is an error.
- **Pinned environments**: the upstream task images `akhatua/cooperbench-<repo>:task<id>`, pinned by
  digest (`image_digests.json`, checked against Docker Hub on 2026-11-09). By default they are pulled
  through `mirror.gcr.io` (Google's public Docker Hub mirror) to avoid Docker Hub's anonymous pull
  limit; `--registry docker.io` writes `FROM akhatua/…@sha256:…` instead. The digest fixes the image
  bytes either way.
- **Three containers per trial** (`environment/docker-compose.yaml`), all from the same image:
  `main` (single agents work here; the coop patches are collected here), `agent1` and `agent2`
  (one per coop agent, like the original's two agent containers). The agent containers reuse
  `main`'s built image, so the agent phase pulls its image once.
- **Grading in a fresh environment**: `[verifier] environment_mode = "separate"`. After the agent
  phase, Harbor collects `/opt/task/submission` (the graded patches) as an artifact, stops the agent
  environment and runs the verifier in a fresh copy of the environment, as the original evaluator
  uses a fresh sandbox. Nothing an agent changed besides its patch reaches the grader.
- **Faithful grading**: `tests/cooperbench_eval.py` is a line-by-line port of the original
  `eval/sandbox.py::test_merged`: the same test-file filter, patch sanitizer, branch setup,
  `git apply` / `--3way`, naive merge, identical-patch short-cut, agent1-alone fallback, `runner.sh`
  invocation, 600 s limit per runner call, and result parser. It runs the original scripts with the
  image's own environment, as `docker exec` does in the original.
- **Oracle**: `solution/solve.sh` applies the task's `combined.patch` (the full upstream PR). For
  `go_chi` task 56 it then applies a 3-line `oracle_fix.patch` (see Notes & Caveats).
- **Custom coop agent** (`cooperbench_autoadapter.coop_agent:CooperBenchCoopAgent`, Scenario 3):
  runs the original `mini_swe_agent_v2` code (agent loop, `coop.yaml` prompts and limits, litellm
  model, `send_message` handling, `GitConnector` + `gh` shim, `MessagingConnector`) vendored
  unchanged except for import paths. Only the transport differs:
  - bash actions go to the `agent1` / `agent2` compose services through Harbor
    (`environment.service_exec`) instead of `docker exec` (same `bash -lc`, same cwd and env);
  - the Redis server is an in-process `fakeredis` server shared by the two agents of a trial.

  Each agent gets only its own `feature.md`, as in the original. Each agent's graded patch is its
  PR diff (`GitConnector.submitted_patch`), uploaded to `main:/opt/task/submission/` when that
  agent finishes. The pair's ids come from the task's `environment/pair.json`, read on the Harbor
  host (it is never inside a container). The agent writes the original's `result.json`, `conversation.json` and
  per-agent trajectories to the trial's agent log directory, and reports token usage to Harbor.
- **Single-agent Harbor agents** (e.g. `claude-code`, `codex`) can also run the tasks: they read
  `instruction.md` (both features), work in `main:/workspace/repo`, and a `[[verifier.collect]]` hook
  takes their diff against the base commit, which is graded as agent1's patch with an empty agent2
  patch (the original's solo grading).

## Generated Task Structure

```
datasets/cooperbench-autoadapter/
└── cooperbench-autoadapter-<repo>-task<id>-f<f1>-f<f2>/
    ├── task.toml                 # name, resources, separate verifier, submission artifact, collect hook
    ├── instruction.md            # short header + both feature.md texts
    ├── environment/
    │   ├── Dockerfile            # FROM the pinned task image; records env/base commit; removes upstream history
    │   ├── docker-compose.yaml   # main + agent1 + agent2
    │   └── pair.json             # {repo, task_id, features} for the coop agent (build context only)
    ├── solution/
    │   ├── solve.sh              # applies combined.patch (+ oracle_fix.patch where present)
    │   ├── combined.patch
    │   └── oracle_fix.patch      # go_chi task56 only
    └── tests/
        ├── test.sh               # runs cooperbench_eval.py, writes /logs/verifier/reward.txt
        ├── cooperbench_eval.py   # port of test_merged
        ├── tests1.patch          # hidden tests of feature f1
        ├── tests2.patch          # hidden tests of feature f2
        └── pair.json
```

Adapter code:

```
src/cooperbench-autoadapter/
├── pyproject.toml, uv.lock, README.md, adapter_metadata.json, parity_experiment.json
├── run_cooperbench-autoadapter.yaml        # oracle (default) / coop agent reference config
└── src/cooperbench_autoadapter/
    ├── adapter.py, main.py                 # task generation
    ├── pair_instruction.py                 # instruction.md layout and its exact inverse
    ├── make_parity_split.py                # parity split selection (seeded)
    ├── image_digests.json, splits/parity.json
    ├── oracle_fixes/                       # oracle-only follow-up patches (go_chi_task/task56.patch)
    ├── coop_agent/                         # Harbor coop agent + vendored mini_swe_agent_v2
    └── task-template/
```

## Run Evaluation / Harness

### Running with Datasets Registry

```bash
# Oracle (reference solutions)
harbor run -d cooperbench/cooperbench-autoadapter -a oracle -e daytona
# Parity subset
harbor run -d cooperbench/cooperbench-autoadapter@parity -a oracle -e daytona
# The CooperBench coop setting: two mini-swe-agent v2 agents per pair (custom agent of this adapter)
uvx --python 3.12 --from "harbor[daytona,adapter]==0.25.0" --with-editable "./src/cooperbench-autoadapter[agent]" \
  harbor run -d cooperbench/cooperbench-autoadapter \
  -a cooperbench_autoadapter.coop_agent:CooperBenchCoopAgent -m openai/<model> -e daytona
# A standard single agent (both features, one agent)
harbor run -d cooperbench/cooperbench-autoadapter -a codex -m "<model>" -e daytona
```

The coop agent runs in the Harbor process, so its dependencies must be installed there (the
`agent` extra, added with `--with-editable` above). It takes `--ak agent_config=<yaml>`
(deep-merged into `coop.yaml`, like the original `--agent-config`) and
`--ak messaging_enabled=false`. Model credentials are read by litellm from the usual variables
(`OPENAI_API_KEY`, `OPENAI_BASE_URL`, ...).

### Using Job Configurations

`run_cooperbench-autoadapter.yaml` runs the oracle on the locally generated dataset; commented
blocks select the coop agent or a single agent.

```bash
# From the repository root
harbor run -c src/cooperbench-autoadapter/run_cooperbench-autoadapter.yaml
# Or without a config, on a locally prepared dataset
harbor run -p datasets/cooperbench-autoadapter -a oracle -e daytona -n 16
# Resume a job (e.g. after infrastructure errors)
harbor job resume -p jobs/<job-name>
```

### Running Individual Trial

```bash
harbor trial start -p datasets/cooperbench-autoadapter/cooperbench-autoadapter-go-chi-task26-f1-f2 -e daytona
harbor trial start -p datasets/cooperbench-autoadapter/cooperbench-autoadapter-go-chi-task26-f1-f2 \
  -a codex -m "<model>" -e daytona
```

## Usage: Create Task Directories

```bash
cd src/cooperbench-autoadapter
uv sync
uv run cooperbench-autoadapter --output-dir ../../datasets/cooperbench-autoadapter          # all 652 pairs
uv run cooperbench-autoadapter --split parity --output-dir ../../datasets/cooperbench-autoadapter-parity
uv run cooperbench-autoadapter --split lite --output-dir /tmp/cb-lite                       # upstream subset
uv run cooperbench-autoadapter --task-ids go_chi_task/26/1_2 --overwrite
```

By default the adapter clones the upstream repository at the pinned commit into a temporary
directory; `--source-dir` uses an existing checkout instead. `--limit`, `--overwrite` and
`--task-ids` (local names, full names, or `<repo>/<task_id>/<f1>_<f2>`) are supported.
`--registry` sets the registry the pinned images are pulled from (default `mirror.gcr.io`;
`docker.io` for Docker Hub).

## Comparison with Original Benchmark (Parity)

Parity compares the original CooperBench harness with this adapter on the same 15 pairs, with the
benchmark's own agent (mini-swe-agent v2 coop loops, `coop.yaml`, step limit 100, messaging on, git
off) and the same model on both sides.

| Agent | Model | Metric | Runs | Dataset Size | Original (mean ± SEM) | Harbor (mean ± SEM) |
|-------|-------|--------|------|--------------|-----------------------|---------------------|
| cooperbench-coop-mini-swe-agent-v2@63b9d44 | openai/deepseek-v4.1-flash | pass_rate (%) | 3 | 15 (2.3% of 652) | 55.56 ± 8.01 | 55.56 ± 5.88 |

Per run (pass rate, %): original 66.67, 60.00, 40.00; Harbor 53.33, 46.67, 66.67. The ranges overlap.

- Parity set (`--split parity`, `splits/parity.json`): 15 of the 652 pairs (2.3%), stratified by
  repository: each of the 12 repositories gets at least one pair, the remaining slots follow each
  repository's share of the 652 pairs (largest remainder), and pairs are drawn uniformly within a
  repository with seed 42. It covers all four languages (Python, Go, TypeScript, Rust). The size is set
  by the time budget: one coop trial takes up to about an hour of agent time.
- Original side: [hanxu12/CooperBench](https://github.com/hanxu12/CooperBench/tree/harbor-parity),
  branch `harbor-parity`. The parity runs used its commit `eb271c8` = upstream `63b9d44` plus the
  evaluator's `core.hooksPath` line (also applied here), the parity subset, and a runner for Daytona
  (later commits change only its README). Its README lists every change and the exact run commands.
- Same on both sides: the upstream task images (pinned digests), the agent code and `coop.yaml`,
  litellm 1.108.3, the model `openai/deepseek-v4.1-flash` through the same OpenAI-compatible
  endpoint, the original evaluation scripts, and 4 CPUs and 12 GB of memory per pair (one pair at a
  time per sandbox on both sides).
- One resource difference: Harbor gives the Rust `typst` tasks 8 CPUs and 16 GB; the original side
  ran the `typst` pair with 4 CPUs and 12 GB and passed it, so the smaller size did not hold the
  original back. The Harbor `typst` failures come from the agents (a time-out, unpushed work, a patch
  that only added a test file), not from builds, which take seconds against the 600 s evaluator limit.
- Metric: pass rate (a pair counts when both features' tests pass on the merged patches), reported as
  mean ± sample SEM over runs.
- Runs: 3 per side on Daytona (Harbor 0.25.0), both sides at the same time: run 1, then runs 2
  and 3 together. Every model call on both sides was served by `deepseek-v4-1-flash-261017`.
- Noise: with 15 pairs, one run's pass rate has a standard error of about 13 points
  (sqrt(p(1-p)/15) at p ≈ 0.55), so single pairs flip between runs on both sides.
- Oracle: the reference solutions pass all 652 tasks (652/652 reward 1, 0 exceptions; Harbor 0.25.0
  on Daytona; one trial hit a Docker Compose start error and was re-run with `harbor job resume -f`).
- Standard agent: Claude Code 2.1.311 with the same model (through the endpoint's Anthropic-format
  API), one agent for both features, solved 11 of the 15 parity tasks (73.3%, one run). This checks
  that the tasks work with a standard Harbor agent; it is not part of the parity comparison.

Links: [original benchmark](https://github.com/cooperbench/CooperBench) ·
[original-side fork](https://github.com/hanxu12/CooperBench/tree/harbor-parity) ·
[adapter PR](https://github.com/harbor-index/adapter-automation/pull/12) · [dataset PR](https://github.com/harbor-framework/harbor-datasets/pull/279) · [parity results](https://huggingface.co/datasets/harborframework/parity-experiments/discussions/283)

Reproduce the Harbor side (from the repository root):

```bash
cd src/cooperbench-autoadapter && uv run cooperbench-autoadapter --split parity \
  --output-dir ../../datasets/cooperbench-autoadapter-parity && cd ../..
uvx --python 3.12 --from "harbor[daytona,adapter]==0.25.0" \
  --with-editable "./src/cooperbench-autoadapter[agent]" \
  harbor run -p datasets/cooperbench-autoadapter-parity \
  -a cooperbench_autoadapter.coop_agent:CooperBenchCoopAgent \
  -m openai/deepseek-v4.1-flash -e daytona -n 15
```

Reproduce the original side (in the fork, see its README "Harbor parity"):

```bash
uv sync --frozen && uv pip install litellm==1.108.3 mcp==2.3.0 && redis-server --daemonize yes
uv run --frozen --no-sync cooperbench run --setting coop -a mini_swe_agent_v2 \
  -m openai/deepseek-v4.1-flash --subset harbor_parity --backend docker -n <run-name>
```

## Notes & Caveats

- **Grading fix: repository git hooks are disabled while grading (both sides).** The evaluator
  commits each patch on a branch before merging. In `react_hook_form` task 85 the repository's
  husky pre-commit hook runs `tsc`, which rejects the gold patch, so the commit failed, the merged
  diff was empty and even the oracle scored 0 (reproduced with the unmodified original evaluator).
  The evaluator now sets `git config core.hooksPath /dev/null` before committing, here and in the
  original-side fork used for parity. Only repositories with active hooks are affected.
- **Oracle fix for `go_chi` task 56.** The task's gold patch builds the `Allow` header of a 405
  response by ranging over a Go map, so the header order is random and feature 1's
  `TestMethodNotAllowed` (which expects `GET, HEAD`) fails in about 1 of 6 runs; feature 5's test is
  order-dependent too. The oracle applies `oracle_fixes/go_chi_task/task56.patch` after
  `combined.patch`, which sorts the allowed methods before writing the header. With it, every
  feature's tests passed 20 of 20 runs. Tests and grading are unchanged; agent solutions are graded
  exactly as before.
- **Image registry.** Docker Hub allows 100 anonymous pulls per hour per IP, and cloud sandboxes
  share egress IPs, so large runs hit `toomanyrequests`. The generated Dockerfiles pull the same
  digests through `mirror.gcr.io`; the original-side parity runs used the same mirror
  (`dockerd --registry-mirror`).
- **Grading follows the original, including its trust in project configuration.** The original
  evaluator drops only test files from a patch and reads pass/fail counts from the test runner's
  output. A patch that changes how tests run (for example the `test` script in `package.json`, or
  `conftest.py`) is applied as it is, in the original and here. This adapter keeps that behavior so
  that scores stay comparable.
- **What agents can see.** `instruction.md` does not name the benchmark or the source task. The
  original coop harness itself shows some names to its agents, and they are kept: the `gh` helper
  script and the `COOPERBENCH_AGENT_ID` variable, the git e-mail `agent@cooperbench.local`, and the
  `## Feature <id>` headings of the original solo prompt. The upstream images also contain
  `/usr/local/bin/runner.sh` (the test command, no tests); the original agents see it too, and
  grading runs in a fresh copy of the environment, so changing it has no effect. The repository's
  history after the base commit is removed at build time (see "Upstream history").
- **Shell environment.** Harbor runs `main` commands in a login shell, which resets `PATH`. The
  evaluator therefore runs the original scripts with the image's recorded environment (as
  `docker exec` does), and `main` restores the image `PATH` for single agents. The two coop agent
  containers keep the original behavior (`bash -lc`, where `go`/`cargo` are not on `PATH`).
- **Time limits.** As in the original, a command may run up to 10800 s. The original ends an agent
  when its container's 2 h lifetime (`container_timeout: 2h`) runs out; here the trial's agent
  timeout is 7200 s. An agent stopped this way submits nothing (in the original its container is
  gone), and the pair is graded with the other agent's patch. A command that never returns (for
  example `grep -r` over `/`, which blocks on `/proc`) therefore costs about 2 h on either side.
- **Messaging.** The coop agents' mailbox is an in-process `fakeredis` server shared by the two
  agents of a trial instead of a Redis server; the message protocol and code are the original's.
- **Upstream history.** The image build removes the repository's remote, tags, other branches and
  reflog (the original `GitConnector` does the same at run time), so no agent can read the real
  implementation from git history. HEAD stays at the base commit.
- **Dependency pins.** The coop agent uses `litellm==1.108.3` and `mcp==2.3.0` (Harbor 0.25.0's
  versions) on both sides; the original lock file's `mcp` 1.26.0 is incompatible with that litellm.
- **Resources.** The original runs containers without limits on its host. Each trial sandbox here
  gets 4 CPUs / 12 GB (typst: 8 CPUs / 16 GB) and 10–30 GB disk, shared by the three containers.
- **Cost.** litellm has no price for some gateway models; the original config ignores cost-tracking
  errors (`cost_tracking: ignore_errors`), so the agent's reported cost can be 0. Token counts are
  reported to Harbor.

## Installation / Prerequisites

- Harbor 0.25.0 (`uv tool install harbor==0.25.0`, or `uvx --from "harbor[daytona,adapter]==0.25.0"`).
- Docker with Compose v2 for `-e docker`, or a Daytona account (`DAYTONA_API_KEY`) for
  `-e daytona` (compose tasks run in Docker-in-Docker sandboxes).
- `git` and Python 3.11+ with `uv` to generate tasks.
- For the coop agent: the `agent` extra (`litellm==1.108.3`, `fakeredis`, …) in Harbor's Python
  environment, and an LLM endpoint litellm can reach.
- Images are 0.1–5.5 GB compressed (up to 10.9 GB unpacked). They are published on Docker Hub and
  pulled through `mirror.gcr.io` by default.

## Troubleshooting

- `ModuleNotFoundError: cooperbench_autoadapter` or `fakeredis`: the coop agent's package is not in
  Harbor's environment; add `--with-editable "./src/cooperbench-autoadapter[agent]"` to the `uvx` command.
- `No module named 'mcp'` from litellm: install the `agent` extra (it includes `mcp`).
- Docker Hub `toomanyrequests` while building: you generated the tasks with `--registry docker.io`;
  regenerate with the default (`mirror.gcr.io`), lower `-n`, or resume the failed trials with
  `harbor job resume -p <job dir> -f <error type>`.
- `go` / `cargo` "command not found" inside `agent1` / `agent2`: expected. The original runs agent
  commands with `bash -lc`, whose `/etc/profile` resets `PATH`; tools are in `/usr/local/go/bin`,
  `/usr/local/cargo/bin`, etc. Grading is not affected.

## Citation

```bibtex
@article{cooperbench2026,
  title={CooperBench: Why Coding Agents Cannot be Your Teammates Yet},
  author={Khatua, Arpandeep and Zhu, Hao and Tran, Peter and Prabhudesai, Arya
          and Sadrieh, Frederic and Lieberwirth, Johann K. and Yu, Xinkai
          and Fu, Yicheng and Ryan, Michael J. and Pei, Jiaxin and Yang, Diyi},
  journal={arXiv preprint arXiv:2601.13295},
  year={2026},
  url={https://arxiv.org/abs/2601.13295}
}
```

## Authors & Contributions

This adapter is developed and maintained by Han Xu (hanxu8@illinois.edu).

- The benchmark, dataset, task images, evaluator and the mini-swe-agent v2 coop agent are by the
  CooperBench authors (MIT license); the vendored agent code keeps its license
  (`coop_agent/LICENSE`).
- Issues and contributions: open an issue or pull request in the adapters repository.
