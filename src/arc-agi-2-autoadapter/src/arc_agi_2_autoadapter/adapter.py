"""Generate Harbor tasks from ARC-AGI-2.

One Harbor task is one test input of one ARC-AGI-2 task (the unit that the
official ARC Prize harness prompts and checks). The instruction is the official
harness prompt for that test input, followed by one paragraph that says where
to write the answer.
"""

from __future__ import annotations

import io
import json
import re
import shutil
import tarfile
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

UPSTREAM_REPO = "arcprize/ARC-AGI-2"
# Pinned upstream data revision (latest commit of main on 2026-11-06).
UPSTREAM_COMMIT = "f3283f727488ad98fe575ea6a5ac981e4a188e49"
UPSTREAM_TARBALL_URL = "https://codeload.github.com/{repo}/tar.gz/{commit}"

SPLITS = ("evaluation", "training")
DEFAULT_SPLIT = "evaluation"

ORG = "arcprize"
DATASET_NAME = "arc-agi-2-autoadapter"

PACKAGE_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = PACKAGE_DIR / "task-template"
# Verbatim copy of src/arc_agi_benchmarking/prompts/system_prompt.txt from
# https://github.com/arcprize/arc-agi-benchmarking (MIT License, Copyright (c)
# 2025 ARC Prize Foundation; see prompts/LICENSE-arc-agi-benchmarking.md).
PROMPT_TEMPLATE_PATH = PACKAGE_DIR / "prompts" / "system_prompt.txt"

# Upstream task IDs are 8 lowercase hex digits; anything else is sanitized.
_SAFE_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class AdapterError(RuntimeError):
    """Raised for invalid input data or options."""


@dataclass(frozen=True)
class ArcTestCase:
    """One test input of one ARC-AGI-2 task."""

    split: str
    arc_task_id: str
    test_index: int
    n_tests: int
    train: list[dict]
    test_input: list[list[int]]
    test_output: list[list[int]]

    @property
    def local_id(self) -> str:
        return f"{self.arc_task_id}-{self.test_index}"

    @property
    def task_name(self) -> str:
        return f"{ORG}/{DATASET_NAME}__{self.local_id}"


def sanitize_task_id(raw: str) -> str:
    """Lowercase; runs of characters other than [a-z0-9] become one hyphen."""
    safe = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")
    if not safe or not _SAFE_ID.match(safe):
        raise AdapterError(f"Cannot build a task name from upstream ID {raw!r}")
    return safe


def build_prompt(train: list[dict], test_input: list[list[int]]) -> str:
    """Rebuild the official prompt exactly like
    ``arc_agi_benchmarking.prompts.prompt_manager.convert_task_pairs_to_prompt``."""
    template = PROMPT_TEMPLATE_PATH.read_text()
    training_examples = ""
    for i, pair in enumerate(train):
        training_examples += f"--Example {i}-- \n\n INPUT: \n\n"
        training_examples += json.dumps(pair["input"]) + "\n\n"
        training_examples += "OUTPUT: \n\n"
        training_examples += json.dumps(pair["output"]) + "\n\n"
    test_input_str = json.dumps(test_input)
    return template.format(
        training_examples=training_examples, test_input=test_input_str
    )


def _validate_grid(grid: object, where: str) -> list[list[int]]:
    if (
        not isinstance(grid, list)
        or not grid
        or not all(isinstance(row, list) and row for row in grid)
        or len({len(row) for row in grid}) != 1
        or not all(
            isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 9
            for row in grid
            for v in row
        )
    ):
        raise AdapterError(f"Invalid ARC grid in {where}")
    return grid


def _validate_task(task: object, where: str) -> dict:
    if (
        not isinstance(task, dict)
        or not isinstance(task.get("train"), list)
        or not task["train"]
        or not isinstance(task.get("test"), list)
        or not task["test"]
    ):
        raise AdapterError(f"Task {where} needs non-empty 'train' and 'test' lists")
    for name in ("train", "test"):
        for i, pair in enumerate(task[name]):
            if not isinstance(pair, dict) or "input" not in pair or "output" not in pair:
                raise AdapterError(f"Task {where} {name}[{i}] needs 'input' and 'output'")
            _validate_grid(pair["input"], f"{where} {name}[{i}].input")
            _validate_grid(pair["output"], f"{where} {name}[{i}].output")
    return task


def load_split_from_dir(data_dir: Path, split: str) -> dict[str, dict]:
    """Load ``<data_dir>/<split>/*.json`` (``data_dir`` is the upstream ``data`` folder)."""
    split_dir = data_dir / split
    if not split_dir.is_dir():
        raise AdapterError(f"Split folder not found: {split_dir}")
    tasks: dict[str, dict] = {}
    for path in sorted(split_dir.glob("*.json")):
        try:
            tasks[path.stem] = json.loads(path.read_text())
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AdapterError(f"Cannot read {path}: {exc}") from exc
    if not tasks:
        raise AdapterError(f"No task files in {split_dir}")
    return tasks


def download_upstream(commit: str = UPSTREAM_COMMIT) -> dict[str, dict[str, dict]]:
    """Download the pinned upstream tarball and return {split: {task_id: task}}."""
    url = UPSTREAM_TARBALL_URL.format(repo=UPSTREAM_REPO, commit=commit)
    request = urllib.request.Request(url, headers={"User-Agent": "harbor-adapter"})
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            payload = response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AdapterError(
            f"Download of {url} failed ({exc}). Retry, or pass --data-dir with a "
            "local copy of the ARC-AGI-2 'data' folder."
        ) from exc
    result: dict[str, dict[str, dict]] = {split: {} for split in SPLITS}
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as tar:
            for member in tar.getmembers():
                parts = Path(member.name).parts
                # <root>/data/<split>/<task_id>.json
                if (
                    member.isfile()
                    and len(parts) == 4
                    and parts[1] == "data"
                    and parts[2] in SPLITS
                    and parts[3].endswith(".json")
                ):
                    extracted = tar.extractfile(member)
                    if extracted is None:
                        raise AdapterError(f"Cannot read {member.name} in {url}")
                    result[parts[2]][parts[3][: -len(".json")]] = json.load(extracted)
    except (tarfile.TarError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise AdapterError(f"Invalid archive from {url}: {exc}") from exc
    for split in SPLITS:
        if not result[split]:
            raise AdapterError(f"No tasks found for split '{split}' in {url}")
        result[split] = dict(sorted(result[split].items()))
    return result


def expand_test_cases(split: str, tasks: dict[str, dict]) -> list[ArcTestCase]:
    cases: list[ArcTestCase] = []
    seen: dict[str, str] = {}
    for raw_id, task in sorted(tasks.items()):
        arc_task_id = sanitize_task_id(raw_id)
        if arc_task_id in seen:
            raise AdapterError(
                f"Upstream IDs {seen[arc_task_id]!r} and {raw_id!r} map to the same name"
            )
        seen[arc_task_id] = raw_id
        _validate_task(task, f"{split}/{raw_id}")
        n_tests = len(task["test"])
        for test_index, pair in enumerate(task["test"]):
            cases.append(
                ArcTestCase(
                    split=split,
                    arc_task_id=arc_task_id,
                    test_index=test_index,
                    n_tests=n_tests,
                    train=task["train"],
                    test_input=pair["input"],
                    test_output=pair["output"],
                )
            )
    return cases


def _render(path: Path, values: dict[str, str]) -> None:
    text = path.read_text()
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    leftover = re.findall(r"\{\{[A-Z_]+\}\}", text)
    if leftover:
        raise AdapterError(f"Unfilled placeholders {leftover} in {path}")
    path.write_text(text)


class ArcAgi2AutoadapterAdapter:
    """Writes one Harbor task directory per ARC-AGI-2 test input."""

    def __init__(
        self,
        output_dir: Path,
        limit: int | None = None,
        overwrite: bool = False,
        task_ids: list[str] | None = None,
        split: str = DEFAULT_SPLIT,
        data_dir: Path | None = None,
        **kwargs,
    ):
        """
        Args:
            output_dir: Directory to write the generated tasks to.
            limit: Generate at most this many Harbor tasks (after filtering).
            overwrite: Replace task directories that already exist.
            task_ids: ARC task IDs (e.g. ``0934a4d8``, all its test inputs) or
                Harbor task IDs (e.g. ``0934a4d8-1``) to generate.
            split: ``evaluation``, ``training`` or ``all``.
            data_dir: Local copy of the upstream ``data`` folder. If unset, the
                pinned upstream commit is downloaded from GitHub.
        """
        if split not in (*SPLITS, "all"):
            raise AdapterError(f"Unknown split '{split}'")
        if limit is not None and limit < 0:
            raise AdapterError("--limit must be 0 or more")
        self.output_dir = Path(output_dir)
        self.limit = limit
        self.overwrite = overwrite
        self.task_ids = task_ids
        self.split = split
        self.data_dir = Path(data_dir) if data_dir else None

    def load_cases(self) -> list[ArcTestCase]:
        splits = SPLITS if self.split == "all" else (self.split,)
        if self.data_dir is not None:
            raw = {s: load_split_from_dir(self.data_dir, s) for s in splits}
        else:
            downloaded = download_upstream()
            raw = {s: downloaded[s] for s in splits}

        cases: list[ArcTestCase] = []
        for split in splits:
            cases.extend(expand_test_cases(split, raw[split]))
        names = [c.local_id for c in cases]
        if len(names) != len(set(names)):
            raise AdapterError("Task IDs collide across splits")

        if self.task_ids:
            wanted = {sanitize_task_id(t) for t in self.task_ids}
            cases = [
                c for c in cases if c.arc_task_id in wanted or c.local_id in wanted
            ]
            found = {c.arc_task_id for c in cases} | {c.local_id for c in cases}
            missing = sorted(wanted - found)
            if missing:
                raise AdapterError(f"Unknown task IDs: {', '.join(missing)}")

        if self.limit is not None:
            cases = cases[: self.limit]
        return cases

    def run(self) -> None:
        cases = self.load_cases()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        written = skipped = 0
        for case in cases:
            task_dir = self.output_dir / case.local_id
            if task_dir.exists() and not self.overwrite:
                skipped += 1
                continue
            self._write_task_atomically(case, task_dir)
            written += 1
        print(
            f"Wrote {written} tasks to {self.output_dir}"
            + (f" (skipped {skipped} existing; use --overwrite)" if skipped else "")
        )

    def _write_task_atomically(self, case: ArcTestCase, task_dir: Path) -> None:
        """Build the task in a temporary folder, then move it into place, so a
        failure never leaves a partial task that a later run would skip."""
        with tempfile.TemporaryDirectory(
            dir=self.output_dir, prefix=f".{case.local_id}."
        ) as tmp:
            staging = Path(tmp) / case.local_id
            self._write_task(case, staging)
            if task_dir.exists():
                shutil.rmtree(task_dir)
            staging.rename(task_dir)

    def _write_task(self, case: ArcTestCase, task_dir: Path) -> None:
        shutil.copytree(TEMPLATE_DIR, task_dir)

        # instruction.md: official prompt + output paragraph.
        _render(
            task_dir / "instruction.md",
            {"ARC_PROMPT": build_prompt(case.train, case.test_input)},
        )

        # task.toml (all values are sanitized IDs or integers).
        _render(
            task_dir / "task.toml",
            {
                "TASK_NAME": case.task_name,
                "SPLIT": case.split,
                "ARC_TASK_ID": case.arc_task_id,
                "TEST_INDEX": str(case.test_index),
                "N_TESTS": str(case.n_tests),
            },
        )

        # tests/expected_output.json: the hidden answer, used only by the verifier.
        (task_dir / "tests" / "expected_output.json").write_text(
            json.dumps(case.test_output) + "\n"
        )

        # solution/solve.sh: writes the expected grid.
        _render(
            task_dir / "solution" / "solve.sh",
            {"EXPECTED_OUTPUT": json.dumps(case.test_output)},
        )
        for script in (
            task_dir / "solution" / "solve.sh",
            task_dir / "tests" / "test.sh",
        ):
            script.chmod(0o755)
