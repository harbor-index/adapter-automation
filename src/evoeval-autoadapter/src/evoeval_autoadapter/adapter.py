"""Generate Harbor tasks from the EvoEval benchmark (https://github.com/evo-eval/evoeval).

Each EvoEval problem becomes one Harbor task. The agent gets the problem's
`prompt` (function signature + docstring) and writes the completed code to
`/workspace/solution.py`. A separate verifier container runs the original
EvoEval checker (`evoeval.eval_test.untrusted_check`, evoeval 0.1.0) on the
problem's test inputs against the outputs of the canonical solution, as
`evoeval.evaluate` does.
"""

from __future__ import annotations

import gzip
import json
import shutil
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

# The EvoEval data release used by evoeval 0.1.0 (`EVOEVAL_VERSION` in evoeval/data.py).
EVOEVAL_VERSION = "v0.1.0"
RELEASE_URL = (
    "https://github.com/evo-eval/evoeval_release/releases/download/{version}/{name}.jsonl.gz"
)

# Datasets that use the default evaluation path of `evoeval.evaluate`
# (string test inputs, base tests only). EvoEval_verbose and EvoEval_concise use
# the HumanEval+ base/plus inputs instead and are not supported here.
SUPPORTED_DATASETS = {
    "EvoEval_difficult": "difficult",
    "EvoEval_creative": "creative",
    "EvoEval_subtle": "subtle",
    "EvoEval_combine": "combine",
    "EvoEval_tool_use": "tool-use",
}
DEFAULT_DATASET = "EvoEval_difficult"
REQUIRED_FIELDS = ("task_id", "prompt", "canonical_solution", "entry_point", "inputs")

ADAPTER_NAME = "evoeval-autoadapter"
TASK_ORG = "evo-eval"

TEMPLATE_DIR = Path(__file__).parent / "task-template"


def _toml_escape(value: str) -> str:
    """Escape text for use inside a TOML basic string ("...")."""
    return json.dumps(value, ensure_ascii=False)[1:-1]


def _problem_number(task_id: str) -> int:
    prefix, _, number = task_id.partition("/")
    if prefix != "EvoEval" or not number.isdigit():
        raise ValueError(f"Unexpected EvoEval task_id {task_id!r}")
    return int(number)


class EvoevalAutoadapterAdapter:
    def __init__(
        self,
        output_dir: Path,
        limit: int | None = None,
        overwrite: bool = False,
        task_ids: list[str] | None = None,
        dataset: str = DEFAULT_DATASET,
        data_file: Path | None = None,
        **kwargs,
    ):
        """
        Args:
            output_dir: Directory to write the generated tasks to.
            limit: Generate at most this many tasks (after `task_ids` filtering).
            overwrite: Replace task directories that already exist.
            task_ids: Only generate these tasks. Accepts upstream IDs
                (`EvoEval/10`), plain numbers (`10`) or Harbor task names.
            dataset: EvoEval dataset to convert (default `EvoEval_difficult`).
            data_file: Local `<dataset>.jsonl(.gz)` to use instead of downloading
                the release file.
        """
        if dataset not in SUPPORTED_DATASETS:
            raise ValueError(
                f"Unsupported dataset {dataset!r}. Supported: {', '.join(SUPPORTED_DATASETS)}"
            )
        if limit is not None and limit < 0:
            raise ValueError(f"--limit must be >= 0, got {limit}")
        self.output_dir = Path(output_dir)
        self.limit = limit
        self.overwrite = overwrite
        self.task_ids = task_ids
        self.dataset = dataset
        self.data_file = Path(data_file) if data_file else None

    # ------------------------------------------------------------------ data
    def _read_source(self) -> tuple[bytes, str]:
        if self.data_file is not None:
            try:
                return self.data_file.read_bytes(), str(self.data_file)
            except OSError as exc:
                raise RuntimeError(f"Cannot read --data-file {self.data_file}: {exc}") from exc
        url = RELEASE_URL.format(version=EVOEVAL_VERSION, name=self.dataset)
        try:
            with urllib.request.urlopen(url, timeout=120) as resp:
                return resp.read(), url
        except (urllib.error.URLError, OSError) as exc:
            raise RuntimeError(
                f"Cannot download {url}: {exc}. Download it manually and pass --data-file."
            ) from exc

    @staticmethod
    def _check_row(problem: object, where: str) -> None:
        if not isinstance(problem, dict):
            raise RuntimeError(f"{where}: expected a JSON object, got {type(problem).__name__}")
        missing = [k for k in REQUIRED_FIELDS if k not in problem]
        if missing:
            raise RuntimeError(f"{where}: missing fields {missing}")
        for key in ("task_id", "prompt", "canonical_solution", "entry_point"):
            if not isinstance(problem[key], str):
                raise RuntimeError(f"{where}: `{key}` must be a string")
        if not isinstance(problem["inputs"], list) or not all(
            isinstance(i, str) for i in problem["inputs"]
        ):
            raise RuntimeError(f"{where}: `inputs` must be a list of strings")
        if "def " + problem["entry_point"] + "(" not in problem["prompt"]:
            raise RuntimeError(f"{where}: prompt does not define `{problem['entry_point']}`")
        try:
            _problem_number(problem["task_id"])
        except ValueError as exc:
            raise RuntimeError(f"{where}: {exc}") from exc

    def load_problems(self) -> list[dict]:
        """Load the dataset rows in upstream order (EvoEval/0, EvoEval/1, ...)."""
        raw, source = self._read_source()
        try:
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            lines = raw.decode("utf-8").splitlines()
        except (OSError, EOFError, UnicodeDecodeError) as exc:
            raise RuntimeError(f"{source} is not a valid (gzipped) JSONL file: {exc}") from exc

        problems = []
        for lineno, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                problem = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"{source}:{lineno}: invalid JSON: {exc}") from exc
            self._check_row(problem, f"{source}:{lineno}")
            problems.append(problem)
        if not problems:
            raise RuntimeError(f"{source} contains no problems")
        ids = [p["task_id"] for p in problems]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise RuntimeError(f"{source}: duplicate task_id values {duplicates}")
        problems.sort(key=lambda p: _problem_number(p["task_id"]))
        return problems

    def task_dir_name(self, upstream_id: str) -> str:
        return f"{ADAPTER_NAME}-{SUPPORTED_DATASETS[self.dataset]}-{_problem_number(upstream_id)}"

    def _selected(self, problems: list[dict]) -> list[dict]:
        if self.task_ids:
            by_key = {}
            for p in problems:
                by_key[p["task_id"]] = p
                by_key[str(_problem_number(p["task_id"]))] = p
                by_key[self.task_dir_name(p["task_id"])] = p
                by_key[f"{TASK_ORG}/{self.task_dir_name(p['task_id'])}"] = p
            unknown = [tid for tid in self.task_ids if tid.strip() not in by_key]
            if unknown:
                raise ValueError(f"Unknown task IDs for {self.dataset}: {', '.join(unknown)}")
            wanted = {by_key[tid.strip()]["task_id"] for tid in self.task_ids}
            problems = [p for p in problems if p["task_id"] in wanted]
        if self.limit is not None:
            problems = problems[: self.limit]
        return problems

    # ------------------------------------------------------------- rendering
    def _render(self, rel_path: str, values: dict[str, str]) -> str:
        text = (TEMPLATE_DIR / rel_path).read_text()
        for key, value in values.items():
            text = text.replace("{{" + key + "}}", value)
        return text

    def _write_task(self, problem: dict, task_dir: Path) -> None:
        upstream_id = problem["task_id"]
        name = self.task_dir_name(upstream_id)
        (task_dir / "environment").mkdir(parents=True)
        (task_dir / "solution").mkdir()
        (task_dir / "tests").mkdir()

        subset = SUPPORTED_DATASETS[self.dataset]
        values = {
            "TASK_NAME": _toml_escape(f"{TASK_ORG}/{name}"),
            "DESCRIPTION": _toml_escape(
                f"{self.dataset} {upstream_id}: complete the Python function "
                f"`{problem['entry_point']}`."
            ),
            "SUBSET_KEYWORD": _toml_escape(f"evoeval-{subset}"),
            "DIFFICULTY": _toml_escape("hard" if subset == "difficult" else "medium"),
            "DATASET": _toml_escape(self.dataset),
            "UPSTREAM_ID": _toml_escape(upstream_id),
            "PARENT": _toml_escape(problem.get("parent", "")),
            "ENTRY_POINT_TOML": _toml_escape(problem["entry_point"]),
        }
        (task_dir / "task.toml").write_text(self._render("task.toml", values))

        instruction = self._render(
            "instruction.md",
            {"PROMPT": problem["prompt"], "ENTRY_POINT": problem["entry_point"]},
        )
        (task_dir / "instruction.md").write_text(instruction)

        shutil.copy2(TEMPLATE_DIR / "environment" / "Dockerfile", task_dir / "environment" / "Dockerfile")

        # Oracle: the canonical solution, assembled exactly as evoeval.evaluate builds
        # the ground truth (prompt + "\n" + canonical_solution).
        (task_dir / "solution" / "solution.py").write_text(
            problem["prompt"] + "\n" + problem["canonical_solution"]
        )
        shutil.copy2(TEMPLATE_DIR / "solution" / "solve.sh", task_dir / "solution" / "solve.sh")

        test_problem = {
            "dataset": self.dataset,
            "task_id": upstream_id,
            "entry_point": problem["entry_point"],
            "prompt": problem["prompt"],
            "canonical_solution": problem["canonical_solution"],
            "inputs": problem["inputs"],
        }
        (task_dir / "tests" / "problem.json").write_text(json.dumps(test_problem, indent=1))
        for fname in ("test.sh", "evaluate.py"):
            shutil.copy2(TEMPLATE_DIR / "tests" / fname, task_dir / "tests" / fname)

        for script in (task_dir / "solution" / "solve.sh", task_dir / "tests" / "test.sh"):
            script.chmod(0o755)

    def generate_task(self, problem: dict) -> Path:
        """Write one task; build it in a staging directory, then swap it in."""
        task_dir = self.output_dir / self.task_dir_name(problem["task_id"])
        with tempfile.TemporaryDirectory(dir=self.output_dir, prefix=".staging-") as tmp:
            staged = Path(tmp) / task_dir.name
            self._write_task(problem, staged)
            if task_dir.exists():
                shutil.rmtree(task_dir)
            staged.rename(task_dir)
        return task_dir

    def run(self) -> None:
        problems = self._selected(self.load_problems())
        self.output_dir.mkdir(parents=True, exist_ok=True)
        skipped = 0
        for problem in problems:
            if (self.output_dir / self.task_dir_name(problem["task_id"])).exists() and not self.overwrite:
                skipped += 1
                continue
            self.generate_task(problem)
        print(
            f"Generated {len(problems) - skipped} {self.dataset} tasks in {self.output_dir}"
            + (f" ({skipped} existing tasks kept; use --overwrite to replace them)" if skipped else "")
        )
