## HLE (Humanity's Last Exam) → Harbor Adapter

## Overview

This adapter converts **HLE (Humanity's Last Exam)** into Harbor tasks.

- **Source:** the Hugging Face dataset [`cais/hle`](https://huggingface.co/datasets/cais/hle), split `test`, read at the pinned revision `5a81a4c7271a2a2a312b9a690f0c2fde837e4c29`. The dataset is gated: accept its terms on Hugging Face before generating tasks.
- **Size:** 2,499 tasks, one per question: the 2,500 questions of the test split minus 1 question whose reference answer is missing (see [Notes & Caveats](#notes--caveats); `--include-unverifiable` adds it back). One reference answer with a typo is corrected. 2,158 tasks are text-only and 341 include an image. 1,908 are `exactMatch` and 591 are `multipleChoice`.
- **Categories:** Math 1,021, Biology/Medicine 280, Computer Science/AI 241, Other 233, Physics 230, Humanities/Social Science 219, Chemistry 164, Engineering 111.
- **Task:** the agent reads the question (and, for 341 tasks, the image file `/app/image.<ext>`) and writes its final response to `/app/answer.txt` in the original response format.
- **Grading:** the original LLM-judge prompt compares the response with the reference answer. Reward is 1 (correct) or 0. The benchmark metric is accuracy, the mean reward.
- **Main adaptation:** the original harness makes one chat call per question. Here an agent gets the same question text and the same response format, and writes the response to a file. The image is a file in the container instead of an inline `image_url`. See [Notes & Caveats](#notes--caveats).

The official HLE variants `cais/hle-diamond` and `cais/hle-rolling` are separate gated datasets. This adapter does not generate them.

## What is HLE (Humanity's Last Exam)?

HLE ([paper](https://lastexam.ai/paper), [arXiv 2501.14249](https://arxiv.org/abs/2501.14249), [code](https://github.com/centerforaisafety/hle), [website](https://lastexam.ai)) is a multi-modal benchmark of 2,500 closed-ended, expert-written questions across dozens of subjects, including mathematics, the humanities and the natural sciences. It was built by the Center for AI Safety and Scale AI with subject-matter experts around the world. Questions are multiple-choice or short-answer with a single verifiable answer. The original evaluation (`hle_eval/run_model_predictions.py`) asks a model to answer each question in an `Explanation / Answer / Confidence` format. A judge model (`hle_eval/run_judge_results.py`, default `o3-mini-2025-01-31`) then decides with a fixed prompt and a structured output whether the extracted final answer matches the reference answer. The headline metric is accuracy, with calibration error as a secondary statistic.

## Adapter Features

- **Data loading:** downloads the `cais/hle` test parquet with `huggingface_hub` at a pinned revision (`--revision` to change it, `--parquet` to read a local copy). Task names are stable: `cais/hle-autoadapter-<hle id>`.
- **Instruction:** the question text verbatim, one line with the image path for image questions, then "Write your final response to the file `/app/answer.txt`." and the original system prompt verbatim (the `Explanation / Answer / Confidence` format).
- **Images:** the base64 data URI of the dataset is decoded into `/app/image.png|jpg|gif|webp`, keeping the original format, so agents that can open image files (for example Claude Code's Read tool) give the image to the model.
- **Environment:** `python:3.11.16-slim` with the pinned Python packages that the HLE authors define for tool-enabled evaluation (`docs/requirements-tools.txt` upstream: numpy, scipy, sympy, pandas, scikit-learn, statsmodels, networkx, mpmath, matplotlib, pillow, pyyaml, requests). 1 CPU, 2 GB memory, 4 GB disk. Agent timeout 1,800 s.
- **No internet for the agent:** `[agent] network_mode = "no-network"`, as in the original tool-enabled evaluation ("no direct internet access"). Harbor still allows an installed agent's own inference endpoint during the agent phase and turns off its native web tools. Agent setup and the verifier keep network access. For an agent whose endpoint Harbor cannot detect, add it with `--allow-agent-host <host>`.
- **LLM judge verifier:** `tests/judge.py` copies the original judge: the same `JUDGE_PROMPT` (byte-identical), the same `ExtractedAnswer` structured-output schema, `beta.chat.completions.parse` with `max_completion_tokens=4096`, and the same yes/no decision. It runs with `openai==1.57.0`, the version pinned by the original repository, in a venv (`/opt/judge`). The judge model comes from `JUDGE_MODEL` (default `gpt-5-mini`) and the endpoint from `OPENAI_BASE_URL` (default `https://api.openai.com/v1`). The judgment (extracted answer, reasoning, confidence) is saved to `/logs/verifier/judgment.json`.
- **Isolated grading:** the verifier runs in a separate, fresh container built from the same environment (`[verifier] environment_mode = "separate"`). Only `/app/answer.txt` is copied over (`artifacts`). The agent cannot touch the judge, its Python runtime or the reference answer, and `test.sh` deletes any old reward before grading.
- **Oracle:** writes the reference answer in the response format. The real judge grades it.
- **Subsets:** `--modality text|image` for the text-only or image-only questions; `--split parity` for the fixed 128-task parity subset (see Parity).

## Generated Task Structure

```
datasets/hle-autoadapter/
├── hle-autoadapter-<hle id>/
│   ├── task.toml                 # name, authors, category/answer type/subject metadata, judge env, timeouts, resources
│   ├── instruction.md            # question + image path (if any) + response format
│   ├── environment/
│   │   ├── Dockerfile            # Python 3.11.16 + tool packages + judge venv; copies the image to /app
│   │   └── image.<ext>           # only for the 341 image questions
│   ├── solution/
│   │   ├── solve.sh              # copies response.txt to /app/answer.txt
│   │   └── response.txt          # reference answer in the response format
│   └── tests/
│       ├── test.sh               # runs the judge, writes /logs/verifier/reward.txt
│       ├── judge.py              # original LLM-judge prompt and decision
│       └── config.json           # question, reference answer, metadata
```

Adapter code:

```
src/hle-autoadapter/
├── README.md
├── adapter_metadata.json
├── parity_experiment.json
├── pyproject.toml
├── uv.lock
├── run_hle-autoadapter.yaml
└── src/hle_autoadapter/
    ├── __init__.py
    ├── adapter.py
    ├── main.py
    └── task-template/
        ├── task.toml
        ├── instruction.md
        ├── environment/Dockerfile
        ├── solution/solve.sh
        └── tests/{test.sh,judge.py}
```

## Run Evaluation / Harness

The verifier calls an OpenAI-compatible judge. Before every run, set `OPENAI_API_KEY`, plus `OPENAI_BASE_URL` if you do not use api.openai.com. Optionally set `JUDGE_MODEL` (default `gpt-5-mini`).

### Running with Datasets Registry

After the dataset is published to the Harbor registry:

```bash
# Oracle (reference answers)
harbor run -d cais/hle-autoadapter

# An agent and model
harbor run -d cais/hle-autoadapter -a <agent_name> -m "<model_name>"

# Parity subset
harbor run -d cais/hle-autoadapter@parity -a <agent_name> -m "<model_name>"
```

### Using Job Configurations

```bash
# From the repository root, after generating datasets/hle-autoadapter
harbor run -c src/hle-autoadapter/run_hle-autoadapter.yaml

# Or with the dataset path
harbor run -p datasets/hle-autoadapter -a <agent_name> -m "<model_name>"

# Resume a job
harbor job resume -p jobs/<job_name>
```

### Running Individual Trial

```bash
harbor trial start -p datasets/hle-autoadapter/hle-autoadapter-<hle id>
harbor trial start -p datasets/hle-autoadapter/hle-autoadapter-<hle id> -a <agent_name> -m "<model_name>"
```

## Usage: Create Task Directories

```bash
cd src/hle-autoadapter
uv sync
export HF_TOKEN=...   # an account that accepted the cais/hle terms
uv run hle-autoadapter --output-dir ../../datasets/hle-autoadapter
```

Flags:
- `--output-dir`: where to write tasks (default `datasets/hle-autoadapter` at the repository root)
- `--limit N`: only the first N tasks (sorted by HLE id)
- `--overwrite`: overwrite existing task directories
- `--task-ids ID [ID ...]`: only these tasks (HLE ids or `hle-autoadapter-<id>` names)
- `--split full|parity`: all 2,499 tasks (default) or the 128-task parity subset
- `--modality all|text|image`: all questions (default), text-only, or image-only
- `--parquet PATH`: read a local copy of the `cais/hle` test parquet
- `--revision REV`: dataset revision (default: the pinned commit)
- `--include-unverifiable`: also generate the excluded question

## Comparison with Original Benchmark (Parity)

**Oracle.** The oracle writes the reference answer and the real judge grades it: 2,499/2,499 tasks pass (Harbor 0.25.0, Daytona; jobs `oracle-v3-text` 2,157/2,157, `oracle-v3-image` 341/341, `oracle-v3-fix` 1/1 for the corrected task).

**Parity.** The original harness is a single model call, so both sides run the same coding agent: Claude Code `2.1.311` with `deepseek-v4.1-flash` (served `deepseek-v4-1-flash-261017`). Claude Code can open the image file with its Read tool, so image questions reach the model as images. The original side is a fork of the original repository that adds an agent runner (`hle_eval/run_agent_predictions.py`); its answers are graded by the original, unchanged `run_judge_results.py`. Both sides use the judge `gpt-5-mini`. On both sides the agent's endpoint is a single-model proxy (`hle_eval/model_proxy/` in the fork): it forwards only Messages API calls for `deepseek-v4.1-flash` without server-side tools, and the agent never gets the gateway key (see Notes & Caveats, "Agents can call their model endpoint").

| Agent | Model | Metric | Number of Runs | Dataset Size | Original Benchmark Performance | Harbor Adapter Performance |
|-------|-------|--------|----------------|--------------|--------------------------------|----------------------------|
| claude-code@2.1.311 | deepseek-v4-1-flash-261017 | accuracy (%) | 3 | 128 tasks (5.1% of 2,499) | 35.16 ± 0.45 | 34.64 ± 0.52 |

Scores are mean ± sample SEM over runs. Runs: original 35.94, 34.38, 35.16; Harbor 35.16, 35.16, 33.59. The ranges overlap. Per task, 9 questions were solved only on the original side and 7 only on the Harbor side. Agent timeouts: 81 (original) and 88 (Harbor) of 384 trials per side; Claude Code's 32,000-token output limit: 24 and 18. Every agent message on both sides was served by `deepseek-v4-1-flash-261017`; the proxy forwarded 10,951 calls, all for `deepseek-v4.1-flash`, and refused 128 calls for other models, server-side web or code tools, other API paths, or without the token. The Harbor verifier recorded the judge as `gpt-5-mini-2025-08-07`. In 30 (original) and 22 (Harbor) trials the agent also sent its own requests to the same model through the proxy; counting these trials as wrong gives original 34.64 ± 0.52 and Harbor 34.38 ± 0.45. An earlier set of 3 runs per side without the proxy gave original 33.07 ± 2.03 and Harbor 34.38 ± 0.78; in 27 of its 768 trials the agent used the gateway key for other models or web tools, so it is not the reported result. Full numbers are in [`parity_experiment.json`](parity_experiment.json).

Same settings on both sides:
- Claude Code installed like Harbor's `claude-code` agent (`apt-get install nodejs npm`, `bootstrap.sh 2.1.311`). It runs as `claude --verbose --output-format=stream-json --disallowedTools WebSearch,WebFetch --permission-mode=bypassPermissions --print` with the instruction on stdin, in `/app`, with `ANTHROPIC_MODEL` and the tier-alias variables set to the model.
- The same instruction text (checked for all questions), the same Dockerfile (Python 3.11.16 + tool packages), 1 CPU / 2 GB / 4 GB, and one Daytona sandbox per question.
- No internet during the agent phase except the model endpoint (the proxy), and 1,800 s per question. A missing `/app/answer.txt` counts as wrong.
- The same judge prompt, schema and decision with `openai==1.57.0` and `pydantic==2.10.3`.

Parity set: 128 of the 2,499 tasks (`--split parity`). The sample is stratified by category × modality × answer type with largest-remainder allocation, drawn with `random.Random(42)` from the questions sorted by id. All 8 categories are covered, 17 image tasks (13.3%) and 23.4% multiple choice, against 13.7% and 23.6% in the full set. The task with the corrected reference answer is not eligible, because the original side grades it against the unfixed answer. Size: with a measured 90th-percentile trial time of about 33 min (the 1,800 s cap plus setup) and about 60 concurrent trials per side, 128 tasks × 3 runs fit the 4-hour budget (the reported runs took 2 h 14 min). The expected run-to-run noise at this size is about 4 points (sqrt(p(1-p)/128) at p ≈ 0.33).

Reproduce the original side (fork https://github.com/MrLYG/hle, branch `harbor-parity`; details in its `HARBOR_PARITY.md`):

```bash
git clone -b harbor-parity https://github.com/MrLYG/hle.git && cd hle && git checkout f0e7359
python3.11 -m venv .venv-runner && .venv-runner/bin/pip install -r requirements.txt -r requirements-harbor-parity.txt
python3.11 -m venv .venv-judge && .venv-judge/bin/pip install -r requirements.txt pyarrow==18.1.0 pydantic==2.10.3 httpx==0.28.1 tqdm
export HF_TOKEN=... DAYTONA_API_KEY=... OPENAI_API_KEY=... OPENAI_BASE_URL=...   # judge endpoint (gpt-5-mini)
export RUNNER_PYTHON=$PWD/.venv-runner/bin/python JUDGE_PYTHON=$PWD/.venv-judge/bin/python JUDGE_MODEL=gpt-5-mini
# agent endpoint: the single-model proxy in front of a gateway serving deepseek-v4.1-flash (Anthropic Messages API)
export UPSTREAM_API_KEY=... UPSTREAM_BASE_URL=...
.venv-runner/bin/python hle_eval/model_proxy/launch_proxy.py start
eval "$(.venv-runner/bin/python hle_eval/model_proxy/launch_proxy.py export-env)"   # sets ANTHROPIC_BASE_URL, ANTHROPIC_API_KEY
cd hle_eval && for r in run1 run2 run3; do ./run_harbor_parity.sh $r harbor_parity_ids.txt 32; done
```

Reproduce the Harbor side:

```bash
cd src/hle-autoadapter && uv run hle-autoadapter --split parity --output-dir ../../datasets/hle-autoadapter-parity && cd ../..
# ANTHROPIC_BASE_URL / ANTHROPIC_API_KEY: the same proxy as for the original side
export ANTHROPIC_API_KEY=... ANTHROPIC_BASE_URL=... OPENAI_API_KEY=... OPENAI_BASE_URL=... JUDGE_MODEL=gpt-5-mini
for r in 1 2 3; do
  harbor run -p datasets/hle-autoadapter-parity -a claude-code -m anthropic/deepseek-v4.1-flash \
    --ak version=2.1.311 --ak disable_web_search=true -e daytona -n 32 \
    --agent-setup-timeout-multiplier 3 --ek auto_snapshot=true --job-name parity-harbor-run$r
done
```

The score of a run is the mean reward over the 128 tasks (Harbor) or the number of judgments with `correct: yes` divided by 128 (original). Estimated cost: about $60 for the reported runs (agent tokens at DeepSeek's published peak prices plus judge calls), and about $120 for all parity work, including the earlier runs without the proxy and the sanity checks.

## Notes & Caveats

- **Judge model.** The original judge `o3-mini-2025-01-31` was not available for this work, so all results here use `gpt-5-mini` (`gpt-5-mini-2025-08-07`) on both sides. The judge prompt, schema and decision rule are unchanged. Set `JUDGE_MODEL=o3-mini-2025-01-31` to use the original judge. Scores can differ slightly between judge models.
- **Agent instead of a single model call.** The original harness sends the response format as a system message and the question (with the image inline) as a user message, in one call. A Harbor task is solved by an agent, so the instruction contains the same question text and the same format text, and asks the agent to write the response to `/app/answer.txt`. The image is a file the agent must open. An agent that cannot read image files cannot see the 341 images.
- **Corrected reference answer (1).** `6725562814a5e4119e612733` (Physics, glyoxal Hückel energies): the dataset answer is `α+9β/5,α+β/5,α+(sqrt41*β)/5,α-(sqrt41*β)`. The fourth energy lacks the `/5`: with h_O = 1 and k_O = 0.8 the secular determinant gives α+9β/5, α+β/5 and α±(√41/5)β, as the question's own rationale describes. With the original reference, the judge accepted a correct answer (with `/5`) in 0 of 5 calls and even the exact reference string in only 2 of 5 to 3 of 10 calls. The adapter uses the corrected answer (`ANSWER_FIXES` in `adapter.py`), which the judge accepted in 10 of 10 calls. `tests/config.json` keeps the original answer. Upstream's `hle-rolling-changes.txt` also lists this question as changed in HLE-Rolling. This task is not in the parity set, because the original side grades it against the unfixed answer.
- **Excluded question (1).** `6713a4c60223609143188d32` (Chemistry, image): the reference answer is the placeholder "Names for compounds A, B, and C". The author's rationale says the product names could not be entered, and the question asks nothing. This is a data problem, not a judge problem. A response that names the three products was accepted in 0 of 5 judge calls, and the exact placeholder in only 9 of 20. No answer can be verified, so the task is excluded by default.
- **Judge reliability on the oracle.** Every other reference answer was accepted in 6 of 6 judge calls (one Harbor oracle run plus five local passes over all answers).
- **Tools and internet.** The container has Python 3.11.16 and the tool packages of the original tool-enabled evaluation. During the agent phase there is no internet except the agent's model endpoint. The original tool evaluation also offers web search/fetch tools behind an answer-leak blocklist. A task cannot ship those, so agents here have no web access.
- **Agents can call their model endpoint.** The agent's model endpoint must stay reachable, and the agent can read its own endpoint URL and API key from its environment. With shell access, it can send its own requests to that endpoint. If the key also reaches other models or server-side tools (web search or fetch), the agent can use them, and agents do try: in an earlier set of parity runs with a gateway key, 27 of 768 trials got answers from other models or web tools. For scores of one model without web access, give the agent an endpoint that serves only that model without server tools. The parity runs used such a proxy (`hle_eval/model_proxy/` in the fork, both sides), which refused 128 calls for other models, tools or paths. Calls to the same model are still possible; they happened in 52 of 768 trials, on both sides alike.
- **Links inside questions.** Six questions contain URLs that are part of the original question text (doi.org, arxiv.org, wikipedia.org, and two news sites): `670ebf7239f1dc9b447a9143`, `671567cd87cd585dc9f613db`, `672189d1c98941f2d2d34400`, `6724bcf944c2c3be8ba3cf72`, `67255f5f8488feffb0078345`, `6747da64aa7f6182ef02efae`. They are kept unchanged. As in the original single-call evaluation, the agent cannot open them.
- **Missing response.** If `/app/answer.txt` is missing or empty, the reward is 0 without a judge call. The original also counts a missing prediction as wrong.
- **Judge failures.** The original script retries unjudged answers when it is run again. The verifier tries the judge call up to 5 times within about 1,100 s (verifier timeout 1,800 s). If the judge service still fails, the verifier exits without a reward, an infrastructure error you can resume. If the judge output cannot be parsed, the reward is 0, as an unjudged prediction counts as wrong in the original metric (its accuracy divides by the number of all questions). The original-side parity script re-runs the original judge script up to 5 times. Answers still unjudged after that count as wrong in the accuracy, as on Harbor, and the script exits with an error so that you can check the judge service. In the parity runs no judgment failed: the original side judged every answer in the first judge run, and on Harbor every judge call succeeded on the first attempt.
- **Calibration error.** The original also reports RMS calibration error over the whole dataset. It is not a per-task reward. Each task's extracted confidence is in `/logs/verifier/judgment.json`, so it can be computed from a job.
- **Data handling.** The dataset authors ask users not to re-share the dataset publicly. Generated tasks contain the questions and reference answers.
- **Cost and time.** Agents spend a long time on these questions. In the parity runs (Claude Code + deepseek-v4.1-flash), the agent worked about 820 s per question on average (median about 600 s), and about a fifth of the trials hit the 1,800 s limit. Each trial also needs about 1–2 minutes of sandbox start, agent install and grading.

## Installation / Prerequisites

- Harbor CLI: `uv tool install harbor==0.25.0` (the version used for all results here).
- Docker, or a cloud sandbox provider (the parity and oracle runs used Daytona).
- Python 3.11+ and `uv`:
  ```bash
  cd src/hle-autoadapter
  uv sync
  ```
- A Hugging Face account that accepted the `cais/hle` terms (`HF_TOKEN`), to generate tasks.
- `OPENAI_API_KEY` (and `OPENAI_BASE_URL` for a non-OpenAI endpoint) for the judge, and the API keys of the agent you run.

## Troubleshooting

- **`GatedRepoError` / 401 while generating tasks:** accept the dataset terms at https://huggingface.co/datasets/cais/hle and set `HF_TOKEN`.
- **Verifier error "Judge service failed; no reward written":** the judge endpoint failed 5 times. Check `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `JUDGE_MODEL`, then resume the job with `harbor job resume -p jobs/<job> -f <error type>`.
- **Agent setup timeouts with Claude Code:** Harbor installs Node.js in the container during agent setup. On slow networks this can exceed the default setup timeout. Add `--agent-setup-timeout-multiplier 3`.
- **Agent cannot reach its model:** the agent phase has no internet. Harbor allows the inference endpoint of installed agents automatically. For other agents, add `--allow-agent-host <host>`.
- **Harbor segfaults on Daytona with many image tasks at once:** with Harbor 0.25.0 (daytona SDK 0.230.0, obstore 0.11.1), creating many sandboxes whose Dockerfile has a build context (the 341 image tasks copy `image.*`) at the same time can crash Harbor (`Fatal Python error: Segmentation fault` in the object-storage upload). Run image tasks with low concurrency (`-n 6` worked), or create the snapshots once with `--ek auto_snapshot=true` at low concurrency and then run with `--ek auto_snapshot=true`. Text tasks have no build context and are not affected.
- **Reward 0 with "no response":** the agent did not write `/app/answer.txt` (for example because it hit the 1,800 s limit).

## Citation

```bibtex
@article{phan2025lastexam,
      title = {A benchmark of expert-level academic questions to assess {AI} capabilities},
      author = {{Center for AI Safety} and {Scale AI} and {HLE Contributors Consortium}},
      journal = {Nature},
      volume = {649},
      pages = {1139--1146},
      year = {2026},
      doi = {10.1038/s41586-025-09962-4},
      eprint = {2501.14249},
      archivePrefix = {arXiv},
      primaryClass = {cs.LG},
      url = {https://arxiv.org/abs/2501.14249}
}
```

## Authors & Contributions

This adapter was built by [Yuangang Li](mailto:liyuangang66@gmail.com). The benchmark is by the Center for AI Safety, Scale AI and the HLE Contributors Consortium.

**Issues and Contributions:**
- Submit issues and pull requests to the adapters repository.
- Follow the repository's coding style and commit guidelines.
