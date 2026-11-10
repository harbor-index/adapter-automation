"""CLI: generate Harbor tasks for CooperBench feature pairs.

uv run cooperbench-autoadapter --output-dir ../../datasets/cooperbench-autoadapter
uv run cooperbench-autoadapter --split parity --output-dir ../../datasets/cooperbench-autoadapter-parity
"""

import argparse
import subprocess
from pathlib import Path

from .adapter import DEFAULT_REGISTRY, CooperbenchAutoadapterAdapter

# Default output dir: <repo>/datasets/<adapter_id>
DEFAULT_OUTPUT_DIR = (
    Path(__file__).resolve().parents[4] / "datasets" / "cooperbench-autoadapter"
)


def main() -> None:
    parser = argparse.ArgumentParser()
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
        help="Only generate these tasks (local task names, full names, or <repo>/<task_id>/<f1>_<f2>)",
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=None,
        help="Existing CooperBench checkout (default: clone the pinned commit into a temp dir)",
    )
    parser.add_argument(
        "--split",
        "--subset",
        dest="subset",
        default="all",
        help="all (652 pairs, default), parity (parity split of this adapter), or an upstream "
        "pair subset: lite, flash, core, flash_10",
    )
    parser.add_argument(
        "--registry",
        default=DEFAULT_REGISTRY,
        help="Registry to pull the digest-pinned task images from (default: mirror.gcr.io, "
        "Google's Docker Hub mirror; use docker.io to pull from Docker Hub directly)",
    )
    args = parser.parse_args()

    try:
        adapter = CooperbenchAutoadapterAdapter(
            args.output_dir,
            overwrite=args.overwrite,
            limit=args.limit,
            task_ids=args.task_ids,
            source_dir=args.source_dir,
            subset=args.subset,
            registry=args.registry,
        )
        adapter.run()
    except subprocess.CalledProcessError as e:
        cmd = " ".join(map(str, e.cmd)) if isinstance(e.cmd, list) else e.cmd
        parser.exit(1, f"error: command failed ({e.returncode}): {cmd}\n")
    except (OSError, ValueError, KeyError) as e:
        # json.JSONDecodeError is a ValueError; missing dataset files raise OSError.
        parser.exit(1, f"error: {type(e).__name__}: {e}\n")


if __name__ == "__main__":
    main()
