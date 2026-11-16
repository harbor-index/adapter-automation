"""Compute the official ARC-AGI-2 score from Harbor job results.

Each Harbor task is one test input of one ARC task, and each Harbor trial is
one attempt (reward 1 = the attempt's grid equals the expected grid). This
script applies the rule of the official ARC Prize scorer
(arcprize/arc-agi-benchmarking, ``scoring/scoring.py``):

* a test input is solved if any of its attempts (at most ``--max-attempts``,
  default 2, the ARC-AGI-2 rule) is correct;
* an ARC task scores (solved test inputs) / (test inputs of the task);
* the final score is the mean of the task scores.

Attempts are chosen per test input: graded trials first (in start order),
then trials that ended without a verifier result. A trial without a verifier
result is usually an infrastructure failure (for example a sandbox that did
not start), so by default the script stops and asks to rerun those trials
(``harbor job resume -p <job> -f <ErrorType>``). ``--count-ungraded-as-incorrect``
scores them as incorrect attempts instead (like null attempts in the harness).
With ``--dataset-dir``, every test input of the dataset must have at least one
trial (``--allow-missing`` scores missing ones as unsolved), and every ARC task
of the dataset is scored. Without it, the ARC tasks seen in the jobs are scored.

Usage (run Harbor with ``-k 2`` for the two ARC-AGI-2 attempts):

    uv run arc-agi-2-autoadapter-score jobs/<job-name> [--dataset-dir datasets/arc-agi-2-autoadapter]
"""

from __future__ import annotations

import argparse
import json
import tomllib
from collections import Counter, defaultdict
from pathlib import Path

TASK_NAME_SEPARATOR = "__"


def parse_local_id(task_name: str) -> tuple[str, int]:
    """``arcprize/arc-agi-2-autoadapter__0934a4d8-1`` -> (``0934a4d8``, 1)."""
    local = task_name.split(TASK_NAME_SEPARATOR, 1)[-1].split("/")[-1]
    arc_task_id, test_index = local.rsplit("-", 1)
    return arc_task_id, int(test_index)


def load_trials(job_dirs: list[Path]) -> list[dict]:
    trials = []
    for job_dir in job_dirs:
        for result_path in sorted(job_dir.glob("*/result.json")):
            data = json.loads(result_path.read_text())
            if "task_name" not in data or "trial_name" not in data:
                continue
            rewards = (data.get("verifier_result") or {}).get("rewards") or {}
            reward = rewards.get("reward")
            trials.append(
                {
                    "task_name": data["task_name"],
                    "trial_name": data["trial_name"],
                    "started_at": data.get("started_at") or "",
                    "reward": float(reward) if reward is not None else 0.0,
                    "has_reward": reward is not None,
                    "exception": (data.get("exception_info") or {}).get(
                        "exception_type"
                    ),
                }
            )
    return trials


def load_test_counts(dataset_dir: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for toml_path in sorted(dataset_dir.glob("*/task.toml")):
        config = tomllib.loads(toml_path.read_text())
        meta = config.get("metadata", {})
        counts[meta["arc_task_id"]] = int(meta["arc_n_test_inputs"])
    return counts


def _attempt_order(trial: dict) -> tuple[int, str]:
    # Graded trials first, then trials without a verifier result.
    return (0 if trial["has_reward"] else 1, trial["started_at"])


def official_score(
    trials: list[dict],
    test_counts: dict[str, int] | None = None,
    max_attempts: int = 2,
    allow_missing: bool = False,
) -> dict:
    """Official ARC-AGI-2 score. ``test_counts`` ({arc_task_id: n_test_inputs})
    fixes the task set; without it, the tasks seen in ``trials`` are scored."""
    attempts: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for trial in trials:
        attempts[parse_local_id(trial["task_name"])].append(trial)

    if test_counts is not None:
        unknown = sorted(
            f"{tid}-{idx}"
            for (tid, idx) in attempts
            if tid not in test_counts or idx >= test_counts[tid]
        )
        if unknown:
            raise ValueError(f"Trials for test inputs not in the dataset: {unknown}")
        n_tests = dict(test_counts)
    else:
        n_tests = {}
        for tid, idx in attempts:
            n_tests[tid] = max(n_tests.get(tid, 0), idx + 1)

    missing = sorted(
        f"{tid}-{idx}"
        for tid, count in n_tests.items()
        for idx in range(count)
        if (tid, idx) not in attempts
    )
    if missing and test_counts is not None and not allow_missing:
        raise ValueError(
            f"{len(missing)} test inputs have no trial (e.g. {missing[:5]}); "
            "run them or pass --allow-missing"
        )
    arc_task_ids = set(n_tests)

    task_scores: dict[str, float] = {}
    pairs: dict[str, dict] = {}
    for arc_id in sorted(arc_task_ids):
        solved = 0
        for idx in range(n_tests[arc_id]):
            pair_trials = sorted(
                attempts.get((arc_id, idx), []), key=_attempt_order
            )[:max_attempts]
            ok = any(t["reward"] >= 1.0 for t in pair_trials)
            solved += ok
            pairs[f"{arc_id}-{idx}"] = {
                "attempts": len(pair_trials),
                "rewards": [t["reward"] for t in pair_trials],
                "ungraded_attempts": sum(1 for t in pair_trials if not t["has_reward"]),
                "solved": ok,
            }
        task_scores[arc_id] = solved / n_tests[arc_id]

    n_tasks = len(task_scores)
    total = sum(task_scores.values())
    used = [t for p in pairs.values() for t in p["rewards"]]
    return {
        "score": total / n_tasks if n_tasks else 0.0,
        "score_sum": total,
        "n_arc_tasks": n_tasks,
        "n_test_inputs": len(pairs),
        "n_attempts_scored": len(used),
        "attempt_accuracy": sum(used) / len(used) if used else 0.0,
        "n_trials_without_reward": sum(1 for t in trials if not t["has_reward"]),
        "n_ungraded_attempts_scored": sum(
            p["ungraded_attempts"] for p in pairs.values()
        ),
        "missing_test_inputs": missing,
        "exceptions": dict(
            sorted(
                Counter(t["exception"] for t in trials if t["exception"]).items()
            )
        ),
        "max_attempts": max_attempts,
        "task_scores": task_scores,
        "test_inputs": pairs,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Official ARC-AGI-2 score (pass@2, averaged per ARC task) from Harbor jobs."
    )
    parser.add_argument("job_dirs", nargs="+", type=Path, help="Harbor job directories")
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        default=None,
        help="Generated dataset (to count test inputs per ARC task, including ones with no trial)",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=2,
        help="Attempts per test input that count (ARC-AGI-2 rule: 2)",
    )
    parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="With --dataset-dir: score test inputs without any trial as unsolved",
    )
    parser.add_argument(
        "--count-ungraded-as-incorrect",
        action="store_true",
        help="Score attempts that have no verifier result as incorrect instead of stopping",
    )
    parser.add_argument("--output", type=Path, default=None, help="Write the full result as JSON")
    args = parser.parse_args()

    trials = load_trials(args.job_dirs)
    if not trials:
        raise SystemExit("No trial results found")
    counts = load_test_counts(args.dataset_dir) if args.dataset_dir else None
    try:
        result = official_score(trials, counts, args.max_attempts, args.allow_missing)
    except ValueError as exc:
        raise SystemExit(f"error: {exc}") from exc
    if result["n_ungraded_attempts_scored"]:
        message = (
            f"{result['n_ungraded_attempts_scored']} scored attempts have no verifier "
            f"result (exceptions: {result['exceptions']})"
        )
        if not args.count_ungraded_as_incorrect:
            raise SystemExit(
                f"error: {message}. Rerun them (harbor job resume -p <job> -f <ErrorType>) "
                "or pass --count-ungraded-as-incorrect"
            )
        print(f"warning: {message}; they count as incorrect")
    print(
        f"ARC-AGI-2 score: {100 * result['score']:.2f}% "
        f"({result['score_sum']:.2f}/{result['n_arc_tasks']} ARC tasks, "
        f"{result['n_test_inputs']} test inputs, {result['n_attempts_scored']} attempts, "
        f"attempt accuracy {100 * result['attempt_accuracy']:.2f}%, "
        f"{result['n_trials_without_reward']} trials without reward)"
    )
    if args.output:
        args.output.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
