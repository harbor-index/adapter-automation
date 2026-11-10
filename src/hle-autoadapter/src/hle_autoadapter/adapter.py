"""Generate Harbor tasks from HLE (Humanity's Last Exam).

Source: the Hugging Face dataset `cais/hle`, split `test` (2,500 questions),
read at a pinned revision. One question becomes one Harbor task:

- instruction.md: the question, a pointer to the image file (if any), and the
  response format of the original system prompt.
- environment/: Python 3.11 with the packages of the original tool-enabled
  evaluation; the question image (if any) is copied to /app/image.<ext>.
- solution/: writes the dataset answer in the response format.
- tests/: the original LLM-judge prompt grades /app/answer.txt.
"""

from __future__ import annotations

import base64
import hashlib
import json
import random
import re
import shutil
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

ADAPTER_NAME = "hle-autoadapter"
TASK_ORG = "cais"
HF_DATASET = "cais/hle"
HF_REVISION = "5a81a4c7271a2a2a312b9a690f0c2fde837e4c29"
HF_FILENAME = "data/test-00000-of-00001.parquet"

PARITY_SEED = 42
PARITY_SIZE = 128

# Reference-answer fixes: (corrected answer, reason). The task keeps the original answer in
# tests/config.json ("original_answer"). Fixed questions are left out of the parity set,
# because the original benchmark grades them against the unfixed answer.
ANSWER_FIXES = {
    # Glyoxal Hückel energies: the fourth energy lacks "/5". With h_O=1, k_O=0.8 the
    # secular determinant gives α+9β/5, α+β/5 and α±(√41/5)β (see the question's rationale).
    "6725562814a5e4119e612733": (
        "α+9β/5,α+β/5,α+(sqrt41*β)/5,α-(sqrt41*β)/5",
        "the fourth energy of the reference lacks the /5 denominator",
    ),
}

# Questions whose reference answer cannot be verified, so the oracle fails. Excluded by
# default; see README.
UNVERIFIABLE = {
    # The reference answer is the placeholder "Names for compounds A, B, and C"; the author's
    # rationale says the product names could not be entered. The question asks nothing.
    "6713a4c60223609143188d32": "reference answer is a placeholder (product names missing), question has no ask",
}

TEMPLATE_DIR = Path(__file__).parent / "task-template"
COLUMNS = ["id", "question", "image", "answer", "answer_type", "raw_subject", "category"]
IMAGE_EXTENSIONS = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
}
_DATA_URI_RE = re.compile(r"^data:(?P<mime>[\w/+.-]+);base64,(?P<data>.*)$", re.DOTALL)


@dataclass(frozen=True)
class HLEQuestion:
    id: str
    question: str
    image: str
    answer: str
    answer_type: str
    raw_subject: str
    category: str
    original_answer: str | None = None  # set when ANSWER_FIXES corrected the answer

    @property
    def has_image(self) -> bool:
        return bool(self.image)

    @property
    def modality(self) -> str:
        return "image" if self.has_image else "text"


_ID_RE = re.compile(r"^[0-9a-z]+$")
REQUIRED_TASK_FILES = (
    "task.toml",
    "instruction.md",
    "environment/Dockerfile",
    "solution/solve.sh",
    "solution/response.txt",
    "tests/test.sh",
    "tests/judge.py",
    "tests/config.json",
)


def task_dir_name(hle_id: str) -> str:
    """Registry-safe task directory name; HLE ids are lowercase hex strings."""
    clean = hle_id.lower()
    if not _ID_RE.match(clean):
        raise ValueError(f"unexpected question id {hle_id!r} (expected lowercase letters/digits)")
    return f"{ADAPTER_NAME}-{clean}"


def task_name(hle_id: str) -> str:
    return f"{TASK_ORG}/{task_dir_name(hle_id)}"


def _toml_str(value: str) -> str:
    """Render a TOML basic string (JSON escaping is valid TOML for these values)."""
    return json.dumps(value, ensure_ascii=False)


def _decode_image(data_uri: str) -> tuple[str, bytes]:
    match = _DATA_URI_RE.match(data_uri.strip())
    if not match:
        raise ValueError("image field is not a base64 data URI")
    mime = match.group("mime").lower()
    if mime not in IMAGE_EXTENSIONS:
        raise ValueError(f"unsupported image type {mime}")
    return IMAGE_EXTENSIONS[mime], base64.b64decode(match.group("data"))


def load_questions(parquet_path: Path | None = None, revision: str = HF_REVISION) -> list[HLEQuestion]:
    """Load the `test` split of cais/hle (gated: needs an accepted HF token)."""
    import pyarrow.parquet as pq

    if parquet_path is None:
        from huggingface_hub import hf_hub_download

        try:
            parquet_path = Path(
                hf_hub_download(
                    repo_id=HF_DATASET,
                    repo_type="dataset",
                    filename=HF_FILENAME,
                    revision=revision,
                )
            )
        except Exception as e:
            raise RuntimeError(
                f"Could not download {HF_DATASET}@{revision}. The dataset is gated: accept its "
                "terms on Hugging Face and set HF_TOKEN (or pass --parquet)."
            ) from e
    try:
        table = pq.read_table(parquet_path, columns=COLUMNS)
    except Exception as e:
        raise RuntimeError(f"Could not read {parquet_path} as the cais/hle test parquet") from e
    questions = []
    for row in table.to_pylist():
        values = {key: (row[key] or "") for key in COLUMNS}
        fix = ANSWER_FIXES.get(values["id"])
        if fix is not None:
            values["original_answer"] = values["answer"]
            values["answer"] = fix[0]
        questions.append(HLEQuestion(**values))
    # Compare the task directory names, so that ids differing only in case also count as duplicates.
    names = [task_dir_name(q.id) for q in questions]
    if len(set(names)) != len(names):
        raise ValueError("duplicate question ids in the source dataset (compared case-insensitively)")
    return questions


def parity_subset(questions: list[HLEQuestion], size: int = PARITY_SIZE, seed: int = PARITY_SEED) -> list[HLEQuestion]:
    """Stratified random sample for parity experiments.

    Strata are (category, modality, answer type). Each stratum gets a share
    proportional to its size (largest-remainder rounding), and tasks are drawn
    with a fixed seed from the questions sorted by id. Questions with a fixed or
    unverifiable reference answer are not eligible.
    """
    strata: dict[tuple[str, str, str], list[HLEQuestion]] = defaultdict(list)
    for q in sorted(questions, key=lambda q: q.id):
        if q.id in ANSWER_FIXES or q.id in UNVERIFIABLE:
            continue  # parity needs tasks graded identically on both sides
        strata[(q.category, q.modality, q.answer_type)].append(q)
    total = sum(len(v) for v in strata.values())
    keys = sorted(strata)
    exact = {k: size * len(strata[k]) / total for k in keys}
    alloc = {k: int(exact[k]) for k in keys}
    remaining = size - sum(alloc.values())
    for k in sorted(keys, key=lambda k: (-(exact[k] - int(exact[k])), k)):
        if remaining <= 0:
            break
        alloc[k] += 1
        remaining -= 1
    rng = random.Random(seed)
    chosen: list[HLEQuestion] = []
    for k in keys:
        chosen.extend(rng.sample(strata[k], min(alloc[k], len(strata[k]))))
    return sorted(chosen, key=lambda q: q.id)


class HleAutoadapterAdapter:
    def __init__(
        self,
        output_dir: Path,
        limit: int | None = None,
        overwrite: bool = False,
        task_ids: list[str] | None = None,
        split: str = "full",
        modality: str = "all",
        parquet_path: Path | None = None,
        revision: str = HF_REVISION,
        include_unverifiable: bool = False,
        **kwargs,
    ):
        if limit is not None and limit < 0:
            raise ValueError("--limit must be zero or a positive number")
        self.output_dir = Path(output_dir)
        self.limit = limit
        self.overwrite = overwrite
        self.task_ids = task_ids
        self.split = split
        self.modality = modality
        self.parquet_path = parquet_path
        self.revision = revision
        self.include_unverifiable = include_unverifiable

    # ------------------------------------------------------------------ selection
    def select(self, questions: list[HLEQuestion]) -> list[HLEQuestion]:
        if not self.include_unverifiable:
            questions = [q for q in questions if q.id not in UNVERIFIABLE]
        if self.split == "parity":
            questions = parity_subset(questions)
        elif self.split != "full":
            raise ValueError(f"unknown split {self.split!r} (use 'full' or 'parity')")
        if self.modality != "all":
            questions = [q for q in questions if q.modality == self.modality]
        questions = sorted(questions, key=lambda q: q.id)
        if self.task_ids:
            wanted = {t.lower().removeprefix(f"{ADAPTER_NAME}-") for t in self.task_ids}
            questions = [q for q in questions if q.id.lower() in wanted]
            missing = wanted - {q.id.lower() for q in questions}
            excluded = missing & set(UNVERIFIABLE)
            if excluded:
                raise ValueError(
                    f"excluded as unverifiable (use --include-unverifiable): {sorted(excluded)}"
                )
            if missing:
                raise ValueError(f"unknown task ids: {sorted(missing)}")
        if self.limit is not None:
            questions = questions[: self.limit]
        return questions

    # ------------------------------------------------------------------ rendering
    def _render_instruction(self, q: HLEQuestion, image_name: str | None) -> str:
        template = (TEMPLATE_DIR / "instruction.md").read_text()
        image_note = (
            f"\nThe image for this question is at `/app/{image_name}`. Open it to see it.\n"
            if image_name
            else ""
        )
        # Fill the image note first so that text inside the question is never re-substituted.
        return template.replace("{{IMAGE_NOTE}}", image_note).replace("{{QUESTION}}", q.question)

    def _render_task_toml(self, q: HLEQuestion) -> str:
        tags = [q.answer_type, q.modality, q.category]
        values = {
            "{{TASK_NAME}}": task_name(q.id),
            "{{HLE_ID}}": q.id,
            "{{CATEGORY}}": _toml_str(q.category),
            "{{TAGS}}": "[" + ", ".join(_toml_str(t) for t in tags) + "]",
            "{{ANSWER_TYPE}}": q.answer_type,
            "{{RAW_SUBJECT}}": _toml_str(q.raw_subject),
            "{{MODALITY}}": q.modality,
        }
        text = (TEMPLATE_DIR / "task.toml").read_text()
        for key, value in values.items():
            text = text.replace(key, value)
        return text

    @staticmethod
    def _oracle_response(q: HLEQuestion) -> str:
        return (
            "Explanation: This is the reference answer from the dataset.\n"
            f"Answer: {q.answer}\n"
            "Confidence: 100%\n"
        )

    @staticmethod
    def _is_complete(q: HLEQuestion, task_dir: Path) -> bool:
        required = list(REQUIRED_TASK_FILES)
        if q.has_image:
            ext, _ = _decode_image(q.image)
            required.append(f"environment/image.{ext}")
        return all((task_dir / rel).is_file() for rel in required)

    def generate_task(self, q: HLEQuestion) -> Path:
        task_dir = self.output_dir / task_dir_name(q.id)
        if task_dir.exists() and not self.overwrite and self._is_complete(q, task_dir):
            return task_dir
        # Write into a temporary sibling directory, then move it into place, so an
        # interrupted run never leaves a partial task that a later run would accept.
        tmp_dir = Path(tempfile.mkdtemp(prefix=f".{task_dir.name}.", dir=self.output_dir))
        try:
            self._write_task(q, tmp_dir)
            if task_dir.exists():
                shutil.rmtree(task_dir)
            tmp_dir.rename(task_dir)
        except Exception as e:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise RuntimeError(f"Failed to generate task for question {q.id}: {e}") from e
        return task_dir

    def _write_task(self, q: HLEQuestion, task_dir: Path) -> None:
        (task_dir / "environment").mkdir(parents=True)
        (task_dir / "solution").mkdir()
        (task_dir / "tests").mkdir()

        image_name = None
        image_copy = ""
        if q.has_image:
            ext, data = _decode_image(q.image)
            image_name = f"image.{ext}"
            (task_dir / "environment" / image_name).write_bytes(data)
            image_copy = f"COPY {image_name} /app/{image_name}\n"

        dockerfile = (TEMPLATE_DIR / "environment" / "Dockerfile").read_text()
        (task_dir / "environment" / "Dockerfile").write_text(
            dockerfile.replace("{{IMAGE_COPY}}\n", image_copy)
        )
        (task_dir / "instruction.md").write_text(self._render_instruction(q, image_name))
        (task_dir / "task.toml").write_text(self._render_task_toml(q))

        shutil.copy2(TEMPLATE_DIR / "solution" / "solve.sh", task_dir / "solution" / "solve.sh")
        (task_dir / "solution" / "response.txt").write_text(self._oracle_response(q))

        shutil.copy2(TEMPLATE_DIR / "tests" / "test.sh", task_dir / "tests" / "test.sh")
        shutil.copy2(TEMPLATE_DIR / "tests" / "judge.py", task_dir / "tests" / "judge.py")
        config = {
            "id": q.id,
            "question": q.question,
            "answer": q.answer,
            "answer_type": q.answer_type,
            "category": q.category,
            "raw_subject": q.raw_subject,
            "has_image": q.has_image,
            "image_sha256": hashlib.sha256(q.image.encode()).hexdigest() if q.has_image else None,
        }
        if q.id in ANSWER_FIXES:
            config["original_answer"] = q.original_answer
            config["answer_fix"] = ANSWER_FIXES[q.id][1]
        (task_dir / "tests" / "config.json").write_text(json.dumps(config, indent=2, ensure_ascii=False))
        for script in (task_dir / "solution" / "solve.sh", task_dir / "tests" / "test.sh"):
            script.chmod(0o755)
        task_dir.chmod(0o755)

    def run(self) -> None:
        questions = self.select(load_questions(self.parquet_path, self.revision))
        self.output_dir.mkdir(parents=True, exist_ok=True)
        for q in questions:
            self.generate_task(q)
        print(f"Generated {len(questions)} tasks in {self.output_dir}")
