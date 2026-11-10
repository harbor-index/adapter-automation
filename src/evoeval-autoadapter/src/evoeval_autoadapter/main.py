"""CLI entry point: `uv run evoeval-autoadapter --output-dir <path> [--dataset EvoEval_difficult]`."""

import argparse
from pathlib import Path

from .adapter import DEFAULT_DATASET, SUPPORTED_DATASETS, EvoevalAutoadapterAdapter

# Default output dir: <repo>/datasets/evoeval-autoadapter
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[4] / "datasets" / "evoeval-autoadapter"


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Harbor tasks from EvoEval.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory to write generated tasks",
    )
    parser.add_argument(
        "--limit",
        type=int,
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
        help="Only generate these task IDs (EvoEval/10, 10, or a Harbor task name)",
    )
    parser.add_argument(
        "--dataset",
        choices=sorted(SUPPORTED_DATASETS),
        default=DEFAULT_DATASET,
        help=f"EvoEval dataset to convert (default: {DEFAULT_DATASET})",
    )
    parser.add_argument(
        "--data-file",
        type=Path,
        default=None,
        help="Local <dataset>.jsonl(.gz) to use instead of downloading the v0.1.0 release file",
    )
    args = parser.parse_args()

    try:
        adapter = EvoevalAutoadapterAdapter(
            args.output_dir,
            overwrite=args.overwrite,
            limit=args.limit,
            task_ids=args.task_ids,
            dataset=args.dataset,
            data_file=args.data_file,
        )
        adapter.run()
    except (ValueError, RuntimeError, OSError) as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    main()
