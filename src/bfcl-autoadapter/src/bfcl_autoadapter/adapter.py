"""Generate Harbor tasks from the BFCL single-turn categories.

One BFCL entry becomes one Harbor task. The agent sees the conversation and the
function docs (with BFCL's language-specific hints, as the original shows them to
the model) and writes its function calls to /app/result.json. The verifier runs the
original BFCL AST checker (or the relevance / irrelevance check) on that file.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

from .bfcl_upstream import add_language_specific_hint, is_java, is_js, is_relevance_or_irrelevance
from .oracle import build_oracle_answer

BFCL_REPO_URL = "https://github.com/ShishirPatil/gorilla.git"
BFCL_COMMIT = "6ea57973c7a6097fd7c5915698c54c17c5b1b6c8"
BFCL_SUBDIR = "berkeley-function-call-leaderboard"
VERSION_PREFIX = "BFCL_v4"

ADAPTER_NAME = "bfcl-autoadapter"
TASK_ORG = "gorilla-llm"

NON_LIVE_CATEGORIES = [
    "simple_python",
    "simple_java",
    "simple_javascript",
    "multiple",
    "parallel",
    "parallel_multiple",
    "irrelevance",
]
LIVE_CATEGORIES = [
    "live_simple",
    "live_multiple",
    "live_parallel",
    "live_parallel_multiple",
    "live_irrelevance",
    "live_relevance",
]
ALL_CATEGORIES = NON_LIVE_CATEGORIES + LIVE_CATEGORIES
CATEGORY_GROUPS = {
    "all": ALL_CATEGORIES,
    "single_turn": ALL_CATEGORIES,
    "non_live": NON_LIVE_CATEGORIES,
    "live": LIVE_CATEGORIES,
}

# Upstream entries that no answer can pass with the original BFCL checker (ground
# truth and function doc disagree). They are left out of the dataset; see README.
EXCLUDED_ENTRIES = {
    "simple_java_36": "`parts` is an ArrayList of `any`, which the Java converter always turns into strings, but the ground truth expects the int 42 and the boolean true.",
    "simple_java_65": "`params` is an ArrayList of `any`, which the Java converter always turns into strings, but the ground truth expects the booleans true.",
    "live_simple_106-63-0": "`auto_loan_payment_start` and `bank_hours_start` are required by the function doc, but their list of accepted values is empty.",
    "live_simple_112-68-0": "Five parameters are required by the function doc, but their list of accepted values is empty.",
    "live_multiple_862-181-3": "The ground truth requires `journey_start_time`, which is not a parameter of `Trains_1_FindTrains`.",
    "live_multiple_964-207-0": "The function doc requires `statement`, but the ground truth only accepts a `question` parameter.",
}

TEMPLATE_DIR = Path(__file__).parent / "task-template"


def sanitize_id(bfcl_id: str) -> str:
    """`live_simple_0-0-0` -> `live-simple-0-0-0` (lowercase, registry-safe)."""
    s = re.sub(r"[^a-z0-9]+", "-", bfcl_id.lower()).strip("-")
    return s


def task_dir_name(bfcl_id: str) -> str:
    return f"{ADAPTER_NAME}__{sanitize_id(bfcl_id)}"


def resolve_categories(selected: list[str] | None) -> list[str]:
    if not selected:
        return list(ALL_CATEGORIES)
    out: list[str] = []
    for item in selected:
        cats = CATEGORY_GROUPS.get(item, [item])
        for c in cats:
            if c not in ALL_CATEGORIES:
                raise ValueError(
                    f"Unknown category {c!r}. Choose from {ALL_CATEGORIES} or {list(CATEGORY_GROUPS)}"
                )
            if c not in out:
                out.append(c)
    return [c for c in ALL_CATEGORIES if c in out]


def _load_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        raise FileNotFoundError(f"BFCL data file not found: {path}")
    rows = []
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}:{lineno}: invalid JSON line: {e}") from e
    return rows


def _load_checker():
    """Import the vendored BFCL checker shipped in the task template."""
    import importlib.util

    tests_dir = TEMPLATE_DIR / "tests"
    if str(tests_dir) not in sys.path:
        sys.path.insert(0, str(tests_dir))
    spec = importlib.util.spec_from_file_location("_bfcl_task_evaluate", tests_dir / "evaluate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BfclAutoadapterAdapter:
    def __init__(
        self,
        output_dir: Path,
        limit: int | None = None,
        overwrite: bool = False,
        task_ids: list[str] | None = None,
        categories: list[str] | None = None,
        bfcl_root: Path | None = None,
        **kwargs,
    ):
        if limit is not None and limit < 0:
            raise ValueError("--limit must be zero or a positive number")
        self.output_dir = Path(output_dir)
        self.limit = limit
        self.overwrite = overwrite
        self.task_ids = task_ids
        self.categories = resolve_categories(categories)
        self.bfcl_root = Path(bfcl_root) if bfcl_root else None
        self._tmpdir: tempfile.TemporaryDirectory | None = None

    # ------------------------------------------------------------------ data

    def _ensure_bfcl_root(self) -> Path:
        if self.bfcl_root is not None:
            root = self.bfcl_root
            if (root / BFCL_SUBDIR).is_dir():
                root = root / BFCL_SUBDIR
            if not (root / "bfcl_eval" / "data").is_dir():
                raise FileNotFoundError(f"{root} does not contain bfcl_eval/data")
            self._check_pinned_data(root)
            return root

        self._tmpdir = tempfile.TemporaryDirectory(prefix="bfcl-src-")
        clone = Path(self._tmpdir.name) / "gorilla"
        print(f"Fetching {BFCL_REPO_URL} at {BFCL_COMMIT} ...")
        commands = [
            ["git", "init", "-q", str(clone)],
            ["git", "-C", str(clone), "remote", "add", "origin", BFCL_REPO_URL],
            ["git", "-C", str(clone), "sparse-checkout", "set", f"{BFCL_SUBDIR}/bfcl_eval/data"],
            ["git", "-C", str(clone), "fetch", "-q", "--depth", "1", "--filter=blob:none", "origin", BFCL_COMMIT],
            ["git", "-C", str(clone), "checkout", "-q", "FETCH_HEAD"],
        ]
        for cmd in commands:
            try:
                subprocess.run(cmd, check=True, capture_output=True, text=True)
            except FileNotFoundError as e:
                raise RuntimeError("git is required to fetch the BFCL data (or pass --bfcl-root)") from e
            except subprocess.CalledProcessError as e:
                raise RuntimeError(
                    f"`{' '.join(cmd)}` failed: {e.stderr.strip() or e}. "
                    "Check the network, or pass --bfcl-root with an existing checkout."
                ) from e
        return clone / BFCL_SUBDIR

    @staticmethod
    def _check_pinned_data(root: Path) -> None:
        """Refuse a checkout whose BFCL data differs from the pinned commit.

        The generated tasks record BFCL_COMMIT, so the data must be exactly that
        commit's data (committed and uncommitted changes are both checked).
        """
        data_rel = "bfcl_eval/data"

        def git(*args: str) -> subprocess.CompletedProcess:
            return subprocess.run(
                ["git", "-C", str(root), *args], capture_output=True, text=True
            )

        try:
            known = git("cat-file", "-e", f"{BFCL_COMMIT}^{{commit}}")
        except FileNotFoundError as e:
            raise RuntimeError("git is required to check --bfcl-root") from e
        if known.returncode != 0:
            raise ValueError(
                f"--bfcl-root {root} is not a git checkout that contains the pinned BFCL "
                f"commit {BFCL_COMMIT}"
            )
        diff = git("diff", "--quiet", BFCL_COMMIT, "--", data_rel)
        untracked = git("ls-files", "--others", "--exclude-standard", "--", data_rel)
        if diff.returncode != 0 or untracked.stdout.strip():
            raise ValueError(
                f"The BFCL data in {root / data_rel} differs from the pinned commit "
                f"{BFCL_COMMIT}; use a clean checkout of that commit (or omit --bfcl-root)."
            )

    def load_entries(self) -> list[dict]:
        root = self._ensure_bfcl_root()
        data_dir = root / "bfcl_eval" / "data"
        entries: list[dict] = []
        for category in self.categories:
            prompts = _load_jsonl(data_dir / f"{VERSION_PREFIX}_{category}.json")
            if is_relevance_or_irrelevance(category):
                answers = [None] * len(prompts)
            else:
                answers = _load_jsonl(data_dir / "possible_answer" / f"{VERSION_PREFIX}_{category}.json")
                if len(answers) != len(prompts):
                    raise ValueError(f"{category}: {len(prompts)} prompts but {len(answers)} answers")
            for prompt, answer in zip(prompts, answers):
                if prompt["id"] in EXCLUDED_ENTRIES:
                    continue
                # In BFCL the prompt and the ground truth files are aligned by index.
                if answer is not None and answer["id"] != prompt["id"]:
                    raise ValueError(f"{category}: id mismatch {prompt['id']} vs {answer['id']}")
                entries.append(
                    {
                        "id": prompt["id"],
                        "test_category": category,
                        "group": "live" if category.startswith("live_") else "non_live",
                        "question": prompt["question"],
                        "function": prompt["function"],
                        "ground_truth": None if answer is None else answer["ground_truth"],
                    }
                )
        names = [task_dir_name(e["id"]) for e in entries]
        if len(names) != len(set(names)):
            raise ValueError("task names are not unique after sanitizing")
        return entries

    # ---------------------------------------------------------------- select

    def _select(self, entries: list[dict]) -> list[dict]:
        if self.task_ids:
            lookup: dict[str, dict] = {}
            for e in entries:
                for key in (e["id"], task_dir_name(e["id"]), sanitize_id(e["id"])):
                    lookup[key] = e
            unknown = [t for t in self.task_ids if t not in lookup]
            if unknown:
                excluded = [t for t in unknown if t in EXCLUDED_ENTRIES]
                hint = f" ({len(excluded)} of them are excluded upstream entries, see README)" if excluded else ""
                raise ValueError(
                    f"Unknown task ids (not in the selected categories): {unknown[:10]}{hint}"
                )
            wanted = {lookup[t]["id"] for t in self.task_ids}
            entries = [e for e in entries if e["id"] in wanted]
        if self.limit is not None:
            entries = entries[: self.limit]
        return entries

    # ---------------------------------------------------------------- render

    @staticmethod
    def _render(template: str, values: dict[str, str]) -> str:
        out = template
        for key, value in values.items():
            out = out.replace("{{" + key + "}}", value)
        return out

    def _write_task(self, entry: dict, checker_module) -> None:
        task_dir = self.output_dir / task_dir_name(entry["id"])
        if task_dir.exists():
            if not self.overwrite:
                return
            shutil.rmtree(task_dir)

        test_category = entry["test_category"]
        raw_functions = entry["function"]
        hinted_functions = add_language_specific_hint(raw_functions, test_category)
        conversation = entry["question"][0]
        if len(entry["question"]) != 1:
            raise ValueError(f"{entry['id']}: expected a single-turn entry")

        spec = {
            "id": entry["id"],
            "test_category": test_category,
            "function": raw_functions,
            "ground_truth": entry["ground_truth"],
        }

        def accepts(answer) -> bool:
            if is_relevance_or_irrelevance(test_category):
                details = checker_module.evaluate_relevance(spec, answer, None)
            else:
                details = checker_module.evaluate_ast(spec, answer, None)
            return bool(details.get("valid"))

        oracle_answer = build_oracle_answer(
            test_category, raw_functions, entry["ground_truth"], accepts
        )

        shutil.copytree(TEMPLATE_DIR, task_dir, ignore=shutil.ignore_patterns("__pycache__"))

        language = "java" if is_java(test_category) else "javascript" if is_js(test_category) else "python"
        values = {
            "TASK_NAME": f"{TASK_ORG}/{task_dir_name(entry['id'])}",
            "BFCL_ID": entry["id"],
            "TEST_CATEGORY": test_category,
            "GROUP": entry["group"],
            "LANGUAGE": language,
            "BFCL_COMMIT": BFCL_COMMIT,
            "CONVERSATION_JSON": json.dumps(conversation, indent=2, ensure_ascii=False),
            "FUNCTIONS_JSON": json.dumps(hinted_functions, indent=2, ensure_ascii=False),
        }
        for rel in ("task.toml", "instruction.md"):
            path = task_dir / rel
            path.write_text(self._render(path.read_text(), values))

        (task_dir / "tests" / "task_spec.json").write_text(
            json.dumps(spec, indent=2, ensure_ascii=False) + "\n"
        )
        (task_dir / "solution" / "result.json").write_text(
            json.dumps(oracle_answer, ensure_ascii=False) + "\n"
        )

    # ------------------------------------------------------------------- run

    def run(self) -> None:
        try:
            entries = self._select(self.load_entries())
            self.output_dir.mkdir(parents=True, exist_ok=True)
            checker_module = _load_checker()
            counts: dict[str, int] = defaultdict(int)
            for entry in entries:
                self._write_task(entry, checker_module)
                counts[entry["test_category"]] += 1
            total = sum(counts.values())
            print(f"Wrote {total} tasks to {self.output_dir}")
            for category in ALL_CATEGORIES:
                if counts.get(category):
                    print(f"  {category}: {counts[category]}")
        finally:
            if self._tmpdir is not None:
                self._tmpdir.cleanup()
