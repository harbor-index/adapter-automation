"""Generate Harbor tasks from CooperBench (https://github.com/cooperbench/CooperBench).

One Harbor task = one CooperBench feature pair ``(repo, task_id, f1 < f2)``, the unit the
benchmark scores (652 pairs in the full set). Pairs are enumerated exactly like the original
``runner/tasks.py::discover_tasks``: every 2-combination of a task's features, or the pairs of a
named subset file (``dataset/subsets/<name>.json``).
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import tempfile
from itertools import combinations
from pathlib import Path

from cooperbench_autoadapter.pair_instruction import render as render_instruction

SOURCE_REPO_URL = "https://github.com/cooperbench/CooperBench.git"
# Pinned upstream commit (main, 2026-09-14): dataset, evaluator and agent code all come from it.
SOURCE_COMMIT = "63b9d44d9f39a02fccf5bf0052db48a917a011fd"

ORG = "cooperbench"
ADAPTER_ID = "cooperbench-autoadapter"

PKG_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = PKG_DIR / "task-template"
DIGESTS_FILE = PKG_DIR / "image_digests.json"
SPLITS_DIR = PKG_DIR / "splits"
# Oracle-only follow-up patches (applied after combined.patch by solve.sh) for tasks whose upstream
# gold patch is non-deterministic. Keyed <repo>/task<id>.patch; see README "Notes & Caveats".
ORACLE_FIXES_DIR = PKG_DIR / "oracle_fixes"

# Upstream pair subsets (dataset/subsets/<name>.json). The team3_* files list 3-agent groups for
# the team setting, which this adapter (two agents per task) does not cover.
UPSTREAM_SUBSETS = ("lite", "flash", "core", "flash_10")
UNSUPPORTED_SUBSETS = ("team3_250", "team3_all")

# Registry the pinned task images are pulled from. The images are published on Docker Hub
# (``akhatua/cooperbench-*``); Docker Hub limits anonymous pulls to 100 per hour per IP, which
# shared cloud egress (Daytona) exhausts within a run. ``mirror.gcr.io`` is Google's public
# pull-through mirror of Docker Hub; the digest pins the exact image bytes either way.
DEFAULT_REGISTRY = "mirror.gcr.io"

LANGUAGE = {
    "dottxt_ai_outlines_task": "python",
    "dspy_task": "python",
    "go_chi_task": "go",
    "huggingface_datasets_task": "python",
    "llama_index_task": "python",
    "openai_tiktoken_task": "python",
    "pallets_click_task": "python",
    "pallets_jinja_task": "python",
    "pillow_task": "python",
    "react_hook_form_task": "typescript",
    "samuelcolvin_dirty_equals_task": "python",
    "typst_task": "rust",
}

# Container resources (applied to the trial sandbox / main container). Rust builds of typst
# need more cores and memory than the Python, Go and TypeScript tasks.
DEFAULT_CPUS, DEFAULT_MEMORY_MB = 4, 12288
RESOURCES_OVERRIDE = {"typst_task": (8, 16384)}


def repo_label(repo: str) -> str:
    """Repo name as used in the task image name (original ``get_image_name``)."""
    return repo.replace("_task", "").replace("_", "-")


def local_task_name(repo: str, task_id: int, f1: int, f2: int) -> str:
    return f"{ADAPTER_ID}-{repo_label(repo)}-task{task_id}-f{f1}-f{f2}"


def storage_mb_for(compressed_bytes: int) -> int:
    """Disk for one sandbox: the unpacked image (about 2x compressed), three container layers, slack."""
    gb = max(10, math.ceil(compressed_bytes * 3 / 1e9) + 4)
    return min(gb, 30) * 1024


class CooperbenchAutoadapterAdapter:
    def __init__(
        self,
        output_dir: Path,
        limit: int | None = None,
        overwrite: bool = False,
        task_ids: list[str] | None = None,
        source_dir: Path | None = None,
        subset: str = "all",
        registry: str = DEFAULT_REGISTRY,
        **kwargs,
    ):
        """
        Args:
            output_dir: Directory to write the generated tasks to.
            limit: Generate at most this many tasks (after filtering, in discovery order).
            overwrite: Replace task directories that already exist.
            task_ids: Only generate these tasks. Accepts local task names
                (``cooperbench-autoadapter-go-chi-task26-f1-f2``), full names
                (``cooperbench/...``) or upstream keys (``go_chi_task/26/1_2``).
            source_dir: An existing CooperBench checkout. Default: clone the pinned commit.
            subset: ``all`` (652 pairs), an upstream subset name (``lite``, ``flash``, ...) or
                ``parity`` (the parity split shipped with this adapter).
            registry: Registry host to pull the digest-pinned task images from
                (default ``mirror.gcr.io``; ``docker.io`` pulls from Docker Hub directly).
        """
        if limit is not None and limit < 0:
            raise ValueError(f"limit must be >= 0, got {limit}")
        self.output_dir = Path(output_dir)
        self.limit = limit
        self.overwrite = overwrite
        self.task_ids = set(task_ids) if task_ids else None
        self.source_dir = Path(source_dir) if source_dir else None
        self.subset = subset
        self.registry = registry.strip().rstrip("/")
        self.digests = json.loads(DIGESTS_FILE.read_text())

    # ------------------------------------------------------------------ source

    def _checkout(self, workdir: Path) -> Path:
        if self.source_dir is not None:
            root = self.source_dir
        else:
            root = workdir / "CooperBench"
            subprocess.run(
                ["git", "clone", "--quiet", SOURCE_REPO_URL, str(root)], check=True
            )
            subprocess.run(
                ["git", "-C", str(root), "checkout", "--quiet", SOURCE_COMMIT],
                check=True,
            )
        if (root / ".git").exists():
            head = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            if head != SOURCE_COMMIT:
                print(
                    f"warning: {root} is at {head}, not the pinned commit {SOURCE_COMMIT}"
                )
        dataset = root / "dataset"
        if not dataset.is_dir():
            raise FileNotFoundError(
                f"{dataset} not found; pass --source-dir pointing at a CooperBench checkout"
            )
        return dataset

    # ------------------------------------------------------------------ discovery

    def _load_subset(self, dataset: Path) -> dict | None:
        if self.subset in ("all", "", None):
            return None
        if self.subset == "parity":
            path = SPLITS_DIR / "parity.json"
        elif self.subset in UPSTREAM_SUBSETS:
            path = dataset / "subsets" / f"{self.subset}.json"
        elif self.subset in UNSUPPORTED_SUBSETS:
            raise ValueError(
                f"subset {self.subset!r} lists 3-agent groups (team setting); this adapter "
                "generates 2-agent feature pairs only"
            )
        else:
            raise ValueError(
                f"unknown subset {self.subset!r}; use all, parity or one of {UPSTREAM_SUBSETS}"
            )
        data = json.loads(path.read_text())
        pairs: dict[tuple[str, int], list[list[int]]] = {}
        for t in data["tasks"]:
            pairs[(t["repo"], int(t["task_id"]))] = [list(p) for p in t["pairs"]]
        return {"tasks": set(pairs), "pairs": pairs}

    def discover(self, dataset: Path) -> list[dict]:
        """Same enumeration and order as the original ``discover_tasks``."""
        subset = self._load_subset(dataset)
        out = []
        for repo_dir in sorted(dataset.iterdir()):
            if not repo_dir.is_dir() or repo_dir.name == "subsets":
                continue
            for task_dir in sorted(repo_dir.iterdir()):
                if not task_dir.is_dir() or not task_dir.name.startswith("task"):
                    continue
                task_id = int(task_dir.name.replace("task", ""))
                key = (repo_dir.name, task_id)
                if subset and key not in subset["tasks"]:
                    continue
                fids = sorted(
                    int(f.name.replace("feature", ""))
                    for f in task_dir.iterdir()
                    if f.is_dir() and f.name.startswith("feature")
                )
                if len(fids) < 2:
                    continue
                if subset:
                    pairs = [
                        sorted(p)
                        for p in subset["pairs"][key]
                        if p[0] in fids and p[1] in fids
                    ]
                else:
                    pairs = [list(p) for p in combinations(fids, 2)]
                for f1, f2 in pairs:
                    out.append(
                        {
                            "repo": repo_dir.name,
                            "task_id": task_id,
                            "features": [f1, f2],
                            "dir": task_dir,
                        }
                    )
        return out

    @staticmethod
    def _keys(item: dict) -> set[str]:
        repo, tid, (f1, f2) = item["repo"], item["task_id"], item["features"]
        local = local_task_name(repo, tid, f1, f2)
        return {local, f"{ORG}/{local}", f"{repo}/{tid}/{f1}_{f2}"}

    def _selected(self, item: dict) -> bool:
        return self.task_ids is None or bool(self._keys(item) & self.task_ids)

    # ------------------------------------------------------------------ generation

    def _pull_ref(self, image: str) -> str:
        if self.registry in (
            "",
            "docker.io",
            "index.docker.io",
            "registry-1.docker.io",
        ):
            return image
        return f"{self.registry}/{image}"

    def _render_task(self, item: dict, task_root: Path) -> None:
        repo, task_id, (f1, f2), src = (
            item["repo"],
            item["task_id"],
            item["features"],
            item["dir"],
        )
        label = repo_label(repo)
        digest = self.digests[f"{repo}/task{task_id}"]
        image = digest["image"]
        cpus, memory_mb = RESOURCES_OVERRIDE.get(
            repo, (DEFAULT_CPUS, DEFAULT_MEMORY_MB)
        )

        text1 = (src / f"feature{f1}" / "feature.md").read_text()
        text2 = (src / f"feature{f2}" / "feature.md").read_text()

        # Bytecode caches appear when the template's eval script is compiled locally; never ship them.
        shutil.copytree(
            TEMPLATE_DIR,
            task_root,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )

        (task_root / "instruction.md").write_text(
            render_instruction([f1, f2], text1, text2)
        )
        # Pair metadata for the coop agent, read on the Harbor host. It is in the build context
        # only and never copied into the image, so agents in the containers cannot see it.
        (task_root / "environment" / "pair.json").write_text(
            json.dumps(
                {"repo": repo, "task_id": task_id, "features": [f1, f2]}, indent=2
            )
            + "\n"
        )

        toml = (task_root / "task.toml").read_text()
        for token, value in {
            "__TASK_NAME__": f"{ORG}/{local_task_name(repo, task_id, f1, f2)}",
            "__REPO_LABEL__": label,
            "__REPO__": repo,
            "__TASK_ID__": str(task_id),
            "__F1__": str(f1),
            "__F2__": str(f2),
            "__IMAGE__": f"{image}@{digest['digest']}",
            "__LANGUAGE__": LANGUAGE.get(repo, "unknown"),
            "__CPUS__": str(cpus),
            "__MEMORY_MB__": str(memory_mb),
            "__STORAGE_MB__": str(storage_mb_for(digest["compressed_bytes_amd64"])),
        }.items():
            toml = toml.replace(token, value)
        (task_root / "task.toml").write_text(toml)

        dockerfile = task_root / "environment" / "Dockerfile"
        dockerfile.write_text(
            dockerfile.read_text()
            .replace("{pull_ref}", self._pull_ref(image))
            .replace("{image}", image)
            .replace("{digest}", digest["digest"])
        )
        compose = task_root / "environment" / "docker-compose.yaml"
        compose.write_text(
            compose.read_text()
            .replace("{feature1}", str(f1))
            .replace("{feature2}", str(f2))
        )

        tests = task_root / "tests"
        shutil.copyfile(src / f"feature{f1}" / "tests.patch", tests / "tests1.patch")
        shutil.copyfile(src / f"feature{f2}" / "tests.patch", tests / "tests2.patch")
        (tests / "pair.json").write_text(
            json.dumps(
                {
                    "repo": repo,
                    "task_id": task_id,
                    "features": [f1, f2],
                    "image": image,
                },
                indent=2,
            )
            + "\n"
        )

        shutil.copyfile(
            src / "combined.patch", task_root / "solution" / "combined.patch"
        )
        oracle_fix = ORACLE_FIXES_DIR / repo / f"task{task_id}.patch"
        if oracle_fix.exists():
            shutil.copyfile(oracle_fix, task_root / "solution" / "oracle_fix.patch")

        for script in (tests / "test.sh", task_root / "solution" / "solve.sh"):
            script.chmod(0o755)

    def run(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="cooperbench-src-") as tmp:
            dataset = self._checkout(Path(tmp))
            discovered = self.discover(dataset)
            items = [i for i in discovered if self._selected(i)]
            if self.task_ids is not None:
                known = set().union(*(self._keys(i) for i in discovered))
                unknown = sorted(self.task_ids - known)
                if unknown:
                    raise ValueError(
                        f"unknown task ids (not in subset {self.subset!r}): {', '.join(unknown)}"
                    )
            if self.limit is not None:
                items = items[: self.limit]
            written = skipped = 0
            for item in items:
                name = local_task_name(item["repo"], item["task_id"], *item["features"])
                task_root = self.output_dir / name
                if task_root.exists() and not self.overwrite:
                    skipped += 1
                    continue
                # Render next to the target and move it into place only when complete, so a
                # failure never leaves a partial task that a later run would skip.
                staging = Path(
                    tempfile.mkdtemp(prefix=f".{name}.", dir=self.output_dir)
                )
                try:
                    self._render_task(item, staging / name)
                    if task_root.exists():
                        shutil.rmtree(task_root)
                    (staging / name).rename(task_root)
                finally:
                    shutil.rmtree(staging, ignore_errors=True)
                written += 1
        print(
            f"wrote {written} tasks to {self.output_dir} (skipped {skipped} existing; subset={self.subset})"
        )
