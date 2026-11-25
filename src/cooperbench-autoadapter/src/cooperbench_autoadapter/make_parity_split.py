"""Select the parity split: N feature pairs, stratified by repository, fixed seed.

Each of the 12 repositories (which also covers the languages: Python, Go, Rust, TypeScript) gets
at least one pair; the remaining slots follow each repository's share of the 652 pairs
(largest-remainder rounding). Pairs inside a repository are drawn uniformly with
``random.Random(seed)``. The result is written in the upstream subset format, so the same file
works as ``dataset/subsets/harbor_parity.json`` on the original side.

    python -m cooperbench_autoadapter.make_parity_split --n 24 --source-dir <CooperBench checkout>
"""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path

from cooperbench_autoadapter.adapter import SPLITS_DIR, CooperbenchAutoadapterAdapter


def allocate(counts: dict[str, int], n: int) -> dict[str, int]:
    """Slots per repository: one each, the rest by share of pairs (largest remainder), never
    more than a repository has."""
    repos = sorted(counts)
    total = sum(counts.values())
    if not len(repos) <= n <= total:
        raise ValueError(
            f"n={n} must be between {len(repos)} (one pair per repository) and {total} (all pairs)"
        )
    alloc = {r: 1 for r in repos}
    rest = n - len(repos)
    quotas = {r: rest * counts[r] / total for r in repos}
    for r in repos:
        # quota < counts[r] because rest < total, so this never exceeds the repository's pairs
        alloc[r] += int(quotas[r])
    order = sorted(repos, key=lambda r: (-(quotas[r] - int(quotas[r])), r))
    left = n - sum(alloc.values())
    while left:
        for r in order:
            if left and alloc[r] < counts[r]:
                alloc[r] += 1
                left -= 1
    return alloc


def select(pairs: list[dict], n: int, seed: int) -> list[dict]:
    by_repo: dict[str, list[dict]] = defaultdict(list)
    for p in pairs:
        by_repo[p["repo"]].append(p)
    alloc = allocate({r: len(v) for r, v in by_repo.items()}, n)
    rng = random.Random(seed)
    chosen = []
    for repo in sorted(by_repo):
        pool = sorted(by_repo[repo], key=lambda p: (p["task_id"], p["features"]))
        chosen.extend(rng.sample(pool, alloc[repo]))
    return sorted(chosen, key=lambda p: (p["repo"], p["task_id"], p["features"]))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, required=True)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--source-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=SPLITS_DIR / "parity.json")
    args = ap.parse_args()

    adapter = CooperbenchAutoadapterAdapter(
        output_dir=Path("."), source_dir=args.source_dir
    )
    dataset = adapter._checkout(Path("."))
    pairs = [
        {"repo": i["repo"], "task_id": i["task_id"], "features": i["features"]}
        for i in adapter.discover(dataset)
    ]
    chosen = select(pairs, args.n, args.seed)

    tasks: dict[tuple, list] = defaultdict(list)
    for p in chosen:
        tasks[(p["repo"], p["task_id"])].append(p["features"])
    out = {
        "name": "harbor_parity",
        "description": (
            f"{args.n}-pair parity subset for the Harbor adapter: stratified by repository "
            f"(>=1 per repo, rest proportional to pair count), uniform within repo, seed={args.seed}"
        ),
        "stats": {
            "tasks": len(tasks),
            "pairs": len(chosen),
            "repos": len({r for r, _ in tasks}),
        },
        "tasks": [
            {"repo": r, "task_id": t, "pairs": ps}
            for (r, t), ps in sorted(tasks.items())
        ],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2) + "\n")
    print(f"wrote {len(chosen)} pairs from {out['stats']['repos']} repos to {args.out}")


if __name__ == "__main__":
    main()
