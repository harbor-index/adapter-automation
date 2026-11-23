"""CLI entry point: generate Harbor tasks for HLE (Humanity's Last Exam).

Examples (run from src/hle-autoadapter/):
    uv run hle-autoadapter --output-dir ../../datasets/hle-autoadapter
    uv run hle-autoadapter --split parity --output-dir /tmp/hle-parity
    uv run hle-autoadapter --task-ids 668825f80a642802bdfeadfa --overwrite

The source dataset `cais/hle` is gated on Hugging Face: accept its terms and set
HF_TOKEN (or run `hf auth login`) before running.
"""

import argparse
from pathlib import Path

from .adapter import HF_REVISION, HleAutoadapterAdapter

# Default output dir: <repo>/datasets/hle-autoadapter
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[4] / "datasets" / "hle-autoadapter"


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
        help="Generate only the first N tasks (after sorting by HLE id)",
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
        help="Only generate these tasks (HLE ids, or task directory names hle-autoadapter-<id>)",
    )
    parser.add_argument(
        "--split",
        choices=["full", "parity"],
        default="full",
        help="'full': all 2,500 questions (default). 'parity': the fixed stratified parity subset",
    )
    parser.add_argument(
        "--modality",
        choices=["all", "text", "image"],
        default="all",
        help="Keep all questions (default), only text-only questions, or only questions with an image",
    )
    parser.add_argument(
        "--parquet",
        type=Path,
        default=None,
        help="Read a local copy of the cais/hle test parquet instead of downloading it",
    )
    parser.add_argument(
        "--revision",
        default=HF_REVISION,
        help="Hugging Face revision of cais/hle to download (default: pinned commit)",
    )
    parser.add_argument(
        "--include-unverifiable",
        action="store_true",
        help="Also generate the question whose reference answer cannot be verified (excluded by default)",
    )
    args = parser.parse_args()

    adapter = HleAutoadapterAdapter(
        args.output_dir,
        overwrite=args.overwrite,
        limit=args.limit,
        task_ids=args.task_ids,
        split=args.split,
        modality=args.modality,
        parquet_path=args.parquet,
        revision=args.revision,
        include_unverifiable=args.include_unverifiable,
    )

    adapter.run()


if __name__ == "__main__":
    main()
