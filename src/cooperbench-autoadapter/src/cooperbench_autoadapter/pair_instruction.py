"""Layout of a task's ``instruction.md`` and its exact inverse.

One Harbor task is one CooperBench feature pair. ``instruction.md`` holds both feature
descriptions (``feature.md`` of each feature, byte for byte) in the layout the original solo
setting uses to join features (``runner/solo.py``)::

    ## Feature <f1>\\n\\n<feature1.md>\\n\\n---\\n\\n## Feature <f2>\\n\\n<feature2.md>

preceded by a short header for single-agent Harbor agents. The header names neither the
benchmark nor the source task. The coop agent (``coop_agent``) knows the pair's feature ids from
the task's ``environment/pair.json`` (harness side, never inside a container), parses the two
descriptions back out and gives each of its two agents only its own feature, as the original coop
runner does.
"""

from __future__ import annotations

HEADER = """# Implement two features

The repository is checked out at `/workspace/repo`. Implement both features described below in
it. Your changes in `/workspace/repo` (committed or not) are taken as one patch against the
starting commit; changes to test files are not part of it, so put your work in source files.
"""


def _start(fid: int) -> str:
    return f"## Feature {fid}\n\n"


def _sep(fid: int) -> str:
    return f"\n\n---\n\n## Feature {fid}\n\n"


def render(features: list[int], text1: str, text2: str) -> str:
    """Render instruction.md for the feature pair ``features`` = [f1, f2]."""
    f1, f2 = features
    out = HEADER + "\n" + _start(f1) + text1 + _sep(f2) + text2
    if parse(out, [f1, f2]) != [text1, text2]:
        raise ValueError(
            f"instruction for features {features} does not round-trip; "
            "a feature text contains a separator"
        )
    return out


def parse(instruction: str, features: list[int]) -> list[str]:
    """Return [feature1.md, feature2.md] from ``instruction`` for ``features`` = [f1, f2]."""
    f1, f2 = (int(x) for x in features)
    start, sep = _start(f1), _sep(f2)
    if instruction.count(sep) != 1:
        raise ValueError(f"expected exactly one separator before feature {f2}")
    head, text2 = instruction.split(sep)
    if head.count(start) != 1:
        raise ValueError(f"expected exactly one heading for feature {f1}")
    text1 = head.split(start, 1)[1]
    return [text1, text2]
