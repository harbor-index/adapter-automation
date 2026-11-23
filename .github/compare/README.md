# Compare auto with human

The workflow `.github/workflows/compare-auto-with-human.yml` compares an adapter that an
agent built (an AutoAdapter pull request in this repository) with the adapter that people
built for the same benchmark in harbor-framework/adapters.

## Use

Comment on the pull request:

```
/compare-auto-with-human
```

Settings can follow the command on the same line. A setting that is not given keeps
the default of the workflow `env`:

```
/compare-auto-with-human timeout-multiplier=20 model=deepseek-v4.1-flash concurrency=20
```

| Setting | Meaning | Default (`env`) |
|---|---|---|
| `timeout-multiplier` | Harbor multiplies every task time limit by this value, on both sides | `TIMEOUT_MULTIPLIER` |
| `model` | model of the agent, the name that the model gateway uses | `AGENT_MODEL` |
| `concurrency` | trials that one run job runs at the same time | `N_CONCURRENT` |
| `verifier-env=NAME=value` | one variable for the verifier of each task, on both sides; it replaces the value of the task.toml; can be given more than one time | none |

Example for a benchmark with an LLM judge: `/compare-auto-with-human verifier-env=JUDGE_MODEL=gpt-5-mini`.

Only owners, members and collaborators of this repository can start it. Comment again
to run it again. The result goes to the tracking sheet and to the run summary; the pull
request gets only the run link.

## What it does

1. `check`: reads the pull request (adapter folder, author, head commit), the latest
   Harbor version and the settings in the comment. No keys.
2. `prepare`: Claude finds the human adapter (same original repository link in its
   README), fetches both published datasets from harbor-framework/harbor-datasets (the
   dataset pull request of the auto adapter, and main for the human one) and pairs the
   tasks by original problem ID. Only the human adapter's parity set is compared. Claude
   finds it in this order: the full set when parity used all tasks; a parity list in the
   human adapter folder; a `parity` version in Harbor's legacy registry; the human parity
   runs on Hugging Face (harborframework/parity-experiments); a list in the human README.
   If there is no list, `build_pairs.py` draws the same number of paired problems with
   random seed 0. `build_pairs.py` checks Claude's files and builds `compare/auto/` and
   `compare/human/`. Anthropic key only.
3. `run`: 10 jobs, 2 sides x 5 agent runs, on Daytona, at most 3 at a time. No oracle run:
   both datasets passed the oracle when they were published.
   Daytona and model keys only.
4. `report`: `stats.py` computes the scores (drop the highest and lowest run, mean ± SEM
   of the rest), Claude judges match / no match and the cause, `report.py` writes
   REPORT.md, and `sheet.py` writes the row in the sheet.

## Files

| File | Job | Purpose |
|---|---|---|
| `prepare.md` | prepare | Instructions for Claude: human adapter, datasets, parity set, pairs |
| `fetch.sh` | prepare | The only command Claude can run: fetch one dataset folder, or list the human parity runs on Hugging Face |
| `build_pairs.py` | prepare | Check Claude's files, keep the human parity set, build the comparison folders |
| `options.py` | check | The settings that follow the command in the comment |
| `job_config.py` | run | Harbor JobConfig for one side and run |
| `stats.py` | report | Scores, kept runs, overlap, per-problem table |
| `analyze.md` | report | Instructions for Claude: verdict and cause |
| `report.py` | report | REPORT.md and the text of the Verification cell |
| `sheet.py` | report | Find or add the row by pull request URL and write it |

Claude can write files in the workspace, so the prepare and report jobs run these
scripts from a read-only copy outside the workspace, and the numbers in the sheet come
from `stats.py`, not from Claude.

## Setup

Repository secrets (Settings > Secrets and variables > Actions > Secrets):

| Secret | Value |
|---|---|
| `DAYTONA_API_KEY` | Daytona key |
| `OPENAI_API_KEY` | model gateway key for the agent |
| `OPENAI_BASE_URL` | model gateway URL, with `/v1` |
| `ANTHROPIC_API_KEY` | key for Claude Code |
| `ANTHROPIC_BASE_URL` | Claude gateway URL, without `/v1` |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | JSON key of a Google service account |

Share the sheet with the service account e-mail as an editor. `CLAUDE_MODEL` and
`CLAUDE_EFFORT` in the workflow `env` set the model and effort of Claude Code; the model
must be one that the Claude gateway serves. Other settings (agent, model, time limit
multiplier, runs, concurrency, retries, error limit, sheet ID) are in the same `env` block.

## Test a change

`issue_comment` runs only the workflow file on the default branch. To test a change to
the workflow before it reaches main, push it to the default branch of a fork, add the
secrets there, open a pull request inside the fork and comment on it.
