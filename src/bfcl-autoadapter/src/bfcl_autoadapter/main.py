"""CLI entry point: generate Harbor tasks for the BFCL single-turn categories.

Examples (run from src/bfcl-autoadapter/):
    uv run bfcl-autoadapter --output-dir ../../datasets/bfcl-autoadapter
    uv run bfcl-autoadapter --output-dir ../../datasets/bfcl-autoadapter --categories non_live
"""

import argparse
import sys
from pathlib import Path

from .adapter import ALL_CATEGORIES, CATEGORY_GROUPS, BfclAutoadapterAdapter

# Default output dir: <repo>/datasets/bfcl-autoadapter
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[4] / "datasets" / "bfcl-autoadapter"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
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
        help="Only generate these tasks (BFCL ids such as simple_python_0, or task folder names)",
    )
    parser.add_argument(
        "--categories",
        nargs="+",
        default=None,
        help=(
            "BFCL test categories or groups to generate (default: all single-turn categories). "
            f"Groups: {', '.join(CATEGORY_GROUPS)}. Categories: {', '.join(ALL_CATEGORIES)}."
        ),
    )
    parser.add_argument(
        "--bfcl-root",
        type=Path,
        default=None,
        help=(
            "Existing checkout of ShishirPatil/gorilla (or its berkeley-function-call-leaderboard "
            "folder). Default: fetch the pinned commit into a temporary folder."
        ),
    )
    args = parser.parse_args()

    try:
        adapter = BfclAutoadapterAdapter(
            args.output_dir,
            overwrite=args.overwrite,
            limit=args.limit,
            task_ids=args.task_ids,
            categories=args.categories,
            bfcl_root=args.bfcl_root,
        )
        adapter.run()
    except (ValueError, FileNotFoundError, RuntimeError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        raise SystemExit(1) from e


if __name__ == "__main__":
    main()
