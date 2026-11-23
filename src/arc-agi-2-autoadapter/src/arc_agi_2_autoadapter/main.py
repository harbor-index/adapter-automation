"""CLI entry point: generate Harbor tasks for ARC-AGI-2.

Examples (from src/arc-agi-2-autoadapter/):
    uv run arc-agi-2-autoadapter --output-dir ../../datasets/arc-agi-2-autoadapter
    uv run arc-agi-2-autoadapter --split training --output-dir /tmp/arc-train
    uv run arc-agi-2-autoadapter --task-ids 0934a4d8 135a2760-0
"""

import argparse
from pathlib import Path

from .adapter import DEFAULT_SPLIT, SPLITS, AdapterError, ArcAgi2AutoadapterAdapter

# Default output dir: <repo>/datasets/arc-agi-2-autoadapter
DEFAULT_OUTPUT_DIR = (
    Path(__file__).resolve().parents[4] / "datasets" / "arc-agi-2-autoadapter"
)


def _non_negative_int(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be 0 or more")
    return number


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate Harbor tasks (one per test input) from ARC-AGI-2."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory to write generated tasks",
    )
    parser.add_argument(
        "--limit",
        type=_non_negative_int,
        default=None,
        help="Generate only the first N tasks",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing tasks",
    )
    parser.add_argument(
        "--task-ids",
        nargs="+",
        default=None,
        help=(
            "Only generate these task IDs: an ARC task ID (e.g. 0934a4d8, all its "
            "test inputs) or a Harbor task ID (e.g. 0934a4d8-1)"
        ),
    )
    parser.add_argument(
        "--split",
        choices=[*SPLITS, "all"],
        default=DEFAULT_SPLIT,
        help=(
            "Upstream split: 'evaluation' (default, 120 tasks / 167 test inputs), "
            "'training' (1000 tasks / 1076 test inputs) or 'all'"
        ),
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help=(
            "Local copy of the upstream ARC-AGI-2 'data' folder. If omitted, the "
            "pinned upstream commit is downloaded from GitHub."
        ),
    )
    args = parser.parse_args()

    try:
        adapter = ArcAgi2AutoadapterAdapter(
            args.output_dir,
            overwrite=args.overwrite,
            limit=args.limit,
            task_ids=args.task_ids,
            split=args.split,
            data_dir=args.data_dir,
        )
        adapter.run()
    except AdapterError as exc:
        raise SystemExit(f"error: {exc}") from exc


if __name__ == "__main__":
    main()
