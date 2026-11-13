---
name: autoadapter
description: Build a Harbor adapter for a new benchmark from scratch without human help, verify it (structure check, oracle, Codex review, parity), and open the adapter, dataset and parity pull requests. Use when the person asks to run an AutoAdapter experiment on a benchmark.
---

# AutoAdapter

Build a Harbor adapter for one benchmark in this repository, from the original benchmark. The run ends when three pull requests are open:

1. the adapter, to `harbor-index/adapter-automation`;
2. the dataset, to `harbor-framework/harbor-datasets`;
3. the parity and oracle results, to the Hugging Face dataset `kendx/parity-experiments`.

The person answers questions only at the start (Step 0). After that, work without help. Stop and ask only for an environment problem (see "When to stop and ask").

This skill works with any coding agent. If your agent cannot ask questions with a tool, ask them as plain text and wait for the answer.

## Settings

These are the defaults. The person can change the parity model and the review in Step 0, and picks the parity task set there.

| Setting | Value |
|---|---|
| Harbor | The latest version at the start of the run. In Step 0, get it with `uvx --python 3.12 --from "harbor[daytona,adapter]@latest" harbor --version`. Then run every Harbor command of this run with that version X: `uvx --python 3.12 --from "harbor[daytona,adapter]==X" harbor <command>` |
| Parity model | `deepseek-v4.1-flash` through the OpenAI-compatible gateway in `OPENAI_BASE_URL` |
| Review | Codex, model `gpt-6-astra`, reasoning effort `xhigh` |
| Parity task cap | When you choose the parity size yourself: at most 100 tasks (all tasks if the scope has 100 or fewer). The parity size of an earlier Harbor adapter (from the scout) is used as it is, even if it is larger than 100 (see "Parity task set") |
| Capacity share | Use at most half of the free capacity, and check it again before each job |
| Adapter name | `<benchmark>-autoadapter`, where `<benchmark>` is the short lowercase name of the benchmark (for example `mybench-autoadapter`). Below, `<adapter>` means this name. Use it for the adapter folder, the dataset folder, the dataset ID, the task names and the Hugging Face folder |
| Title prefix | `[AutoAdapter Experiment] ` on every pull request |

The keys are environment variables of the shell that started you: `DAYTONA_API_KEY`, `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `GH_TOKEN`, `HF_TOKEN`. Harbor, `gh` and `hf` read them from the environment. Never print their values, never write them to a file, and never pass them on a command line.

## Do not copy an existing adapter

This benchmark may already have a Harbor adapter or a Harbor dataset made by other people. Build yours from the original benchmark. Do not copy, read or reuse an existing Harbor adapter or Harbor dataset of this benchmark. One deliberate break of these rules makes the run invalid. The only exception is the prior-work scout (see "Prior-work scout"): a subagent reads the dataset size and the parity size of an existing adapter, and you get only these numbers.

Where you may work:

- Read and write files only in this repository. Do not open, list or search any other folder on this computer, with any tool or command. Tools may use their own caches (for example uv and codex); that is not a break.
- In this repository, use only the `main` branch that was cloned and your own branches. Do not fetch, check out or read other branches, tags or commits.
- Change files only in `src/<adapter>/`, in the git-ignored output folders at the root (`datasets/`, `jobs/`) and in `.publish/`. Do not change `skills/`, `docs/`, `scripts/`, `AGENTS.md`, `README.md`, `.github/` or `tests/`.
- The docs in this repository replace some benchmark names with `[removed]`. Do not try to find the removed names.

Other adapters:

- You may read the adapters of other benchmarks in `harbor-framework/adapters` as examples. You may list the folder names in `src/`, then open an adapter by its name.
- Before you open one, check its name against the forbidden names (Step 0). Never open an adapter of this benchmark.
- Do not search the code of these repositories, and do not read their history or pull request lists. The results can show this benchmark.

For this benchmark, do not open (only the scout may read what "Prior-work scout" names):

- its folders and pull requests in `harbor-framework/adapters`, `harbor-index/adapters` and `harbor-framework/harbor`;
- its dataset in `harbor-framework/harbor-datasets` (formerly `laude-institute/harbor-datasets`), in the Hugging Face datasets `harborframework/harbor-datasets`, `harborframework/parity-experiments` and `kendx/parity-experiments`, and in the Harbor registry (do not download or run it, for example with `harbor run -d` or `harbor datasets`);
- forks of the original benchmark that add Harbor support, and Harbor-related branches or pull requests in the original repository.

Also do not open anything in `harbor-index/adapter-automation` except its `main` branch: no other branches, pull requests, issues or forks of other people.

Do not search the web for an existing Harbor adapter or Harbor dataset of this benchmark. If a search result shows one, do not open it.

You may use: the benchmark links from Step 0, papers, the original evaluation code, other agent frameworks, the general Harbor documentation, and the documentation of tools and packages.

In Publish you push to some of the repositories above. Even then, do not open their files or pull requests for this benchmark. Clone `harbor-datasets` only as described in Publish (sparse, so that only `datasets/<adapter>` comes to the disk).

If you see content of an existing adapter or dataset of this benchmark by accident (for example in a search snippet): stop reading it, do not use it, and write in `REPORT.md` what you saw, where and when. The person decides later whether the run is still valid.

## Prior-work scout

Other people may already have adapted this benchmark. Two numbers about their work are useful: the size of their dataset and the size of their parity task set. Their implementation is not allowed: using how they built it would be copying. So a subagent, in its own context, looks up these two numbers and gives you only them.

Run the scout in Step 0.2, after round 1. If your agent cannot start a subagent with its own context, skip the scout. Do not look yourself; treat the result as `found: no`.

Give the scout the benchmark name, the forbidden names (Step 0.3) and this task:

> Find out whether a Harbor adapter of <benchmark> exists, and report only its dataset size and its parity size.
>
> - Look in `harbor-framework/harbor` (folder `adapters/`) and `harbor-framework/adapters` (folder `src/`), on their default branch. Use `gh api` to list the folder names, and match them against the forbidden names. Do not clone, and do not write files.
> - In a matching adapter folder, read only these two files with `gh api`: `adapter_metadata.json` (in each `harbor_adapter` entry: `split`, `adapted_benchmark_size`, `parity_benchmark_size`) and `parity_experiment.json` (`adapted_benchmark_size`, `parity_benchmark_size`). Read nothing else: no code, task files, READMEs or results.
> - Answer with exactly these lines and nothing else:
>   ```
>   found: yes | no
>   dataset_tasks: <split>=<integer>, one per split (for example full=500), or unknown
>   parity_tasks: <split>=<integer>, one per split, or unknown
>   ```
> - Do not add links, paths, folder names, authors, agents, models, scores, notes or anything else.

Rules for you, the lead agent:

- Use only the scout's three lines. Do not ask the scout anything else, do not open its sources, and do not start a second scout.
- The numbers set the defaults for the scope and the parity size (see Step 0.2). Everything else comes from the original benchmark: how you read the data, how you build and grade the tasks, which parity agent you choose and which parity tasks you pick. "Do not copy an existing adapter" still applies to you in full.
- If the scout's lines do not settle the scope or the parity size, do not decide it yourself. The person decides in Round 2 (see Step 0.2).
- You know only the scout's three lines, not how the existing adapter was built. Do not mention where they came from (the existing adapter or the scout) in the adapter code, the README, `parity_experiment.json`, `adapter_metadata.json` or the pull request texts; `DECISIONS.md` and `REPORT.md` record it. Describe what you did: the scope, the parity task set (its size, the categories it covers, and `--split parity` to generate it) and how you picked it. Do not write a reason that is not true.

## When to stop and ask

After Step 0, stop and ask the person only for an environment problem:

- a key is missing or does not work, or a permission is missing;
- a server or service does not work (Daytona, the model gateway, GitHub, Hugging Face, Docker);
- the benchmark needs a resource that you do not have, for example GPUs or more capacity. Do not reduce the scope to avoid it. Report what is missing and wait for the person to fix it;
- the measured parity time is far above the estimate that the person picked, and the person chose "stop and ask" for this case (see "Parity task set").

Write each stop and its answer in `src/<adapter>/REPORT.md`. Make all other decisions yourself.

## Step 0: Check and ask

### 0.1 Check the environment

Check each item. If one fails, stop and tell the person the exact problem and the fix.

1. The five key variables are set. Check with `printenv <NAME> >/dev/null` for each name; do not print the values. If you run inside Codex and the variables are missing, Codex may hide variables whose names contain KEY, SECRET or TOKEN. The person can allow them in `~/.codex/config.toml`:
   ```toml
   [shell_environment_policy]
   ignore_default_excludes = true
   ```
2. `gh auth status` and `hf auth whoami` show a logged-in account.
3. `origin` is the person's fork of `harbor-index/adapter-automation` (`gh repo view --json parent`), the current branch is `main`, and `git rev-parse HEAD` equals the latest commit of `main` in `harbor-index/adapter-automation` (`gh api repos/harbor-index/adapter-automation/commits/main --jq .sha`). If the fork is behind, ask the person to sync it.
4. `git status` is clean, `src/` contains no adapter folder, and `git branch -a` shows only `main` and `origin/main`.
5. Harbor: get the latest version X (see Settings). Use only this version for the whole run, even if a newer version comes out.
6. `git config user.name` and `git config user.email` are set. They are the contributor for `adapter_builders` and the credits.
7. Capacity:
   - Daytona: run `python skills/autoadapter/scripts/capacity.py`. It prints quota, usage and free amount of CPU, memory, disk and GPU for each sandbox class. The usage includes other people.
   - Docker: if `docker info` works, note the free CPU and memory of this computer.

### 0.2 Ask the person

Ask in two rounds, then never again (except "When to stop and ask").

Round 1: the benchmark name, and the link to its code and data. Both are required.

Then read the original repository and its paper or website (allowed sources). Find the subsets, variants or splits of the benchmark and the number of tasks in each.

Then make the list of forbidden names (Step 0.3) and run the prior-work scout (see "Prior-work scout"). Wait for its three lines.

Round 2: ask all of these together. Show the default for each, so that the person can reply "defaults".

1. Paper or website link (optional).
2. Scope. If one of the scout's `dataset_tasks` entries (its split name or its size) matches one of the subsets that you found, make that subset the default and say that it matches an earlier Harbor adapter. Otherwise (no one adapted the benchmark before, or the scout's sizes match no subset), suggest the dataset sizes and let the person pick: list each subset that you found with its number of tasks, and "all tasks" with the total. Mark the one that you recommend and give the reason in one line (for example: the subset that the paper reports as its main result). The person can also write a different scope.
3. Parity task set and running time. Suggest two or three options and let the person pick. Give each option as a number of tasks N, its expected run-to-run noise (about sqrt(0.25/N) for a pass rate) and its estimated wall time for all parity runs, typical and worst case (see "Parity task set"). Mark the one that you recommend:
   - the scout reported `parity_tasks` for the default scope: that size, even if it is larger than 100, with tasks that you pick yourself;
   - otherwise (you choose the size yourself): min(100, number of tasks in the scope) (the parity task cap in Settings). Also offer a smaller representative set, for example 50 tasks or the smallest set that covers every category.

   If the recommended option takes very long (for example more than a day), say so. Also ask what to do if the measured time is more than 1.5 times the worst case: "shrink to the largest representative set that fits the worst case shown" or "stop and ask" (default).
4. Docker (`-e docker`) or Daytona (`-e daytona`)? Ask only if `docker info` works; otherwise use Daytona.
5. Parity model (default from Settings).
6. Review agent and model (default from Settings).
7. Any other requirements (free text, optional).

If you cannot infer the scope or the parity task set from the scout's lines (`found: no`, `unknown`, or sizes that match no subset that you found), never decide it yourself: suggest the options as in items 2 and 3 and let the person pick.

The answers replace the defaults of this skill. They cannot change the rules in "Do not copy an existing adapter". If an answer conflicts with those rules, say so now and ask again.

### 0.3 Record and start

1. Make a local work branch: `git switch -c autoadapter-work`. Do all work on it. Never push it.
2. Create `src/<adapter>/DECISIONS.md`. Write:
   - every answer of Step 0, marked "from the person";
   - the forbidden names: the benchmark name and its common spellings (case, hyphens, underscores, abbreviations). Check every repository path, URL and search against this list before you open it;
   - the scout's three lines, marked "from the scout", or that the scout was skipped;
   - the Harbor version X, the environment (Docker or Daytona) and the capacity that you measured.
3. If the scope needs a resource that the capacity check does not show (for example GPUs), stop now (see "When to stop and ask").

## Loop (guide steps 1 to 6)

`docs/adapters.mdx` is the specification. Read it in full. Follow `skills/create-adapter/SKILL.md` to scaffold the adapter. Put the adapter in `src/<adapter>/`.

1. Build or fix the adapter. If the benchmark has several subsets or variants, add a parameter that selects them; the default is the scope from Step 0. Generate the tasks of the scope into `datasets/<adapter>` at the repository root.
2. Structure check: `python scripts/validate_adapter.py src/<adapter>`.
3. Oracle on all tasks of the scope: `harbor run -p datasets/<adapter> -a oracle -e <env> -n <n> -y`. If a flag does not work, read `harbor run --help`.
4. Review: `harbor adapter review -p src/<adapter> -a codex -m <review model> -o src/<adapter>/review/round-<N>.md` (or the review agent from Step 0). If the review model is not available, stop and ask (environment problem).
5. Parity, when steps 2 to 4 find no problems. See "Parity".
6. If a step finds a problem, find the cause, fix it, and start again at step 1. For parity, use the debug playbook in guide step 5. There is no limit on the number of rounds.
7. Go to Publish only when all of these are true:
   - the structure check has 0 errors;
   - the oracle passes on all tasks;
   - the review has no failures, except items about upload, registration and pull requests;
   - the parity ranges overlap.

### Capacity and sandboxes (every job)

- Before each Harbor job, check the free capacity again (Daytona: `capacity.py`; Docker: this computer). Compute how many trials fit: for each resource, free amount divided by what one task needs (`[environment]` in `task.toml`); take the smallest. Use at most half of that number as `-n`. In parity, split it between the two sides.
- Daytona: label every sandbox of your jobs, for example `--ek 'labels={"autoadapter_owner": "<github user>", "autoadapter_job": "<job name>"}'`. Other people use the same key. Delete only sandboxes with your labels, and delete your sandboxes when a job ends or fails.
- If a job fails because of the infrastructure (sandbox start timeout, network error), run only those trials again (`harbor job resume -p <job dir> -f <error type>`). Do not re-run trials because of their score.

## Parity (guide steps 4 and 5)

Guide step 4 tells you to ask the team for a parity plan. Do not ask. Use this plan:

- Model: the parity model from Step 0, on both sides. Do not use a different model. If it is a reasoning model, its reasoning tokens count toward `max_tokens`, so a small `max_tokens` gives empty answers.
- Agent: select it yourself with the agent scenarios in guide step 4. The original benchmark may have no agent framework. Write the selection and the reason in `DECISIONS.md`.
- Runs: follow the guide order on both sides, with both sides at the same time: sanity check on 5 to 10 tasks, then 1 full run, then 3 runs in total per side.
- Matching: the run-score ranges of the two sides must overlap. Report mean ± sample SEM.
- The oracle always runs on all tasks of the scope, not only on the parity task set.

### Parity task set

The person picks the parity task set in Round 2 from your options. Estimate the running time before Round 2, and check it after the sanity check.

1. Time per task before Round 2 (t), typical and worst case, in minutes:
   - Estimate from the original benchmark: its timeouts and step limits (the worst case), run times reported in the paper or the repository, the number of agent steps times the time per model call, and the time to build the environment and run the tests.
   - Write how you estimated it in `DECISIONS.md`.
2. Concurrency per side (C): from the capacity check of Step 0 and the resources that one task needs (from the original benchmark's requirements; rough at this point). See "Capacity and sandboxes".
3. Wall time: T(N) = 2 × ceil(N / C) × t, plus the sanity check (one more round of 5 to 10 tasks). Run 1 runs alone; runs 2 and 3 run at the same time, so the 3 runs take 2 rounds.
4. Options for Round 2: see Step 0.2, item 3. Write the options, their numbers and the person's choice in `DECISIONS.md`.
5. Check after the sanity check. Record the time per task on each side; use the slower side and a high value (the 90th percentile). Compute T(N) again for the chosen N, and write it in `DECISIONS.md`. If it is more than 1.5 times the worst case shown in Round 2, do what the person chose for this case in Round 2: shrink to the largest representative set whose time fits the worst case shown (it must still cover every category), or stop and ask with the new numbers. Do not shorten timeouts and do not drop slow tasks.
6. Keep it representative. The set must include every category of the benchmark (for example difficulty, language or subset), in proportion. Select with a fixed random seed. Never pick tasks because they are fast, easy or cheap. Write the expected run-to-run noise for this N in `DECISIONS.md` (for a pass rate p: about sqrt(p(1-p)/N)).
7. Use the same task IDs on both sides and in every re-run. If the set is smaller than the scope, support `--split parity` in `main.py` to generate exactly this set, describe the selection in the README and in `parity_experiment.json` (`parity_benchmark_size`, `parity_sampling_rate`), and request the `parity` tag in the pull requests.

## Publish (guide steps 7 to 9)

`<me>` is the person's GitHub user (`gh api user -q .login`). Add `.publish/` to `.git/info/exclude`, and put all clones and temporary files of Publish there.

1. Freeze. On `autoadapter-work`, commit all work and write the commit ID in `REPORT.md`. After this, change only the link fields and `REPORT.md`.
2. Adapter pull request (guide step 9).
   ```
   git switch -c autoadapter-<adapter> origin/main
   git checkout autoadapter-work -- <files>
   ```
   Take only the files of the "Adapter code directory" layout in `docs/adapters.mdx`, plus `uv.lock` if the adapter has one. Leave out `DECISIONS.md`, `REPORT.md`, `review/`, `validation/` and `original_parity/`; the README must not refer to them. Commit, then:
   ```
   git push -u origin autoadapter-<adapter>
   gh pr create --repo harbor-index/adapter-automation --base main --head <me>:autoadapter-<adapter> --title "<title>" --body-file .publish/adapter-pr.md
   ```
3. Dataset pull request (guide step 8).
   ```
   gh repo fork harbor-framework/harbor-datasets --clone=false
   git clone --filter=blob:none --sparse https://github.com/<me>/harbor-datasets.git .publish/harbor-datasets
   cd .publish/harbor-datasets && git config core.autocrlf false && git sparse-checkout set datasets/<adapter>
   git remote add upstream https://github.com/harbor-framework/harbor-datasets.git && git fetch --filter=blob:none upstream
   ```
   If the fork already exists, `gh repo fork` only says so. Make the branch `autoadapter-<adapter>` from `upstream/main`, copy `datasets/<adapter>` (with its `dataset.toml`), commit, push to `origin`, and open the pull request to `harbor-framework/harbor-datasets` the same way. Do not open other folders of this repository.
4. Parity results (guide step 7). Upload them to `kendx/parity-experiments` with `skills/upload-parity-experiments/SKILL.md`.
5. Original-side fork. If you forked the original benchmark for parity (guide step 4), push your branch `harbor-parity` to the person's fork, update its README (guide step 9), and use its URL as `original_parity_repo`.
6. Links. Put the URLs of the three pull requests in the link fields and in the Links sections of the pull request bodies (`gh pr edit <url> --body-file <file>`). Commit and push the branches again.
7. Do not open drafts, do not request reviews, and do not mention anyone. Never merge a pull request, not even your own.

## Pull request texts

Rules for every title and body:

- Write as the contributor, for the reviewer: short sentences, plain words, each fact once.
- Only true numbers from the result files. Put details in the README and point to it (Notes & Caveats).
- No implementation details (internal file formats, user accounts, markers). No internal process (review rounds, `DECISIONS.md`, `REPORT.md`). No experiment context: do not compare with or refer to another adapter (including the scout's sizes), and do not write that an agent built it (the title prefix says that this is an experiment).

(a) Hugging Face `kendx/parity-experiments`. Title: `[AutoAdapter Experiment] Add parity experiments for <adapter>`. Body:

```
Parity and oracle results for the `<adapter>` adapter (harbor-index/adapter-automation#<n>), in `adapters/<adapter>/`.
- `harbor_parity/` and `original_parity/`: <agent@version> + `<parity model>` (served `<served model ID>`), <tasks>, 3 runs per side.
- `oracle/`: Harbor oracle job, <x>/<N>.
- `results_collection/`: `result_{original,harbor}_trial{1,2,3}.json`.
<metric>, mean ± sample SEM: original <m ± s> (<runs>), Harbor <m ± s> (<runs>). The ranges overlap.
Original-side fork: <URL, if any>. Dataset: harbor-framework/harbor-datasets#<n> (registry ID `<org>/<adapter>`).
```

(b) `harbor-framework/harbor-datasets`. Title: `[AutoAdapter Experiment] Add <adapter> dataset (<scope>, <N> tasks)`. Sections:

- `## Summary`: the dataset ID, the number of tasks, the adapter pull request, and the command that generated the dataset.
- `## Checks`: the oracle result; every `task.toml` has a stable name (give the pattern); the task digests in `dataset.toml` match the tasks.
- `## Parity`: the agent and model, the tasks, the runs table (Side | Runs | Mean ± SEM), "The ranges overlap.", and the parity results link.
- `## Requested registry tag`: `v1.0` (and `parity`, if you used a parity task set).
- Last line: "Credits: adapter by <contributor>. Benchmark by <authors> (<Benchmark>)."

(c) `harbor-index/adapter-automation`. Title: `[AutoAdapter Experiment] [Ready for Review] Adapter: <adapter>`. Sections:

- `## Summary`: "This PR adds `<adapter>`, a Harbor adapter for **<Benchmark>** ([code](...), [paper](...))." Then 3 bullets: Benchmark (scope, number of tasks, exclusions), Task (what the agent does, one line), Grading (how a task is scored, one line).
- `## Oracle`: one sentence and the result table that Harbor printed.
- `## Parity`: why this agent (one or two sentences), the model and the tasks, the runs table (Side | Runs (<metric>) | Mean ± SEM), "The ranges overlap.", the standard CLI agent check if you ran one, and the parity cost.
- `## Differences from the original`: "Both parity sides use the same changes. The README (Notes & Caveats) explains each one." Then one plain sentence per difference, and whether it changes a score.
- `## Links`: original benchmark, original-side fork (if any), dataset pull request, parity results.
- `## Requested registry tag`: `v1.0` for all <N> tasks (and `parity`, if you used a parity task set).

## Other rules

- Make each decision yourself and write it with its reason in `src/<adapter>/DECISIONS.md`.
- If your agent supports subagents, use them for work that can run separately, for example: study the original benchmark (data format, evaluation code, variants); diagnose failed oracle tasks or parity differences; check each review finding against `docs/adapters.mdx` before you fix it. Make the final decisions and the fixes yourself.
- If the review and the guide do not agree, follow the guide.
- Write only true results. Do not invent numbers or links.
- `DECISIONS.md`, `REPORT.md`, `review/` and the job results stay in this workspace. They are not part of any pull request.

## REPORT.md

At the end, write `src/<adapter>/REPORT.md` with:

- the Step 0 answers and the capacity at the start;
- the scout's three lines (or that the scout was skipped), and how you used them for the scope and the parity task set;
- the number of tasks and the oracle result;
- the number of rounds and the review result of each round;
- the parity setup (agents, model, runs), the parity task set (all tasks, or the set, its size and how it was chosen), the time estimate shown in Round 2 and how you made it, the estimate after the sanity check, the real parity time, and the results of both sides;
- the review agent, model and CLI version;
- the Harbor version;
- Docker or Daytona;
- each stop for an environment problem;
- each time you saw content of an existing adapter or dataset of this benchmark by accident;
- the open problems;
- the freeze commit;
- the URLs of the pull requests.
