#!/usr/bin/env python3
"""Combine the numbers and Claude's verdict into REPORT.md and result.json.

Inputs: --stats (trusted stats.json from stats.py), compare/meta.json (prepare job),
work/verdict.json and work/analysis.md (Claude), and the workflow env
(PREPARE_RESULT, PR_URL, PR_SHA, AUTHOR, ADAPTER, HARBOR_VERSION, RUN_URL, AGENT, AGENT_MODEL,
TIMEOUT_MULTIPLIER, VERIFIER_ENV).
result.json holds the text for the Verification cell:
  human: 66.67 ± 0.67
  auto: 66.33 ± 1.45
  match | no match (infra|adapter|unclear): <cause> | failed: <cause>
If Claude's verdict is missing or invalid, the fixed rule from stats.json is used.
"""
import argparse
import json
import os
import sys
from pathlib import Path

VERDICTS = {"match", "no match", "failed"}
CAUSES = {"infra", "adapter", "unclear"}


def load_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def one_line(text, limit=300):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def claude_verdict(raw):
    """Return (verdict, cause, summary) or None when Claude's file is not usable."""
    if not isinstance(raw, dict) or raw.get("verdict") not in VERDICTS:
        return None
    cause = raw.get("cause")
    if raw["verdict"] == "no match" and cause not in CAUSES:
        cause = "unclear"
    if raw["verdict"] != "no match":
        cause = None
    return raw["verdict"], cause, one_line(raw.get("summary"))


def verdict_line(verdict, cause, summary):
    if verdict == "match":
        return "match"
    if verdict == "no match":
        return f"no match ({cause}): {summary}" if summary else f"no match ({cause})"
    return f"failed: {summary}" if summary else "failed"


def score_lines(stats):
    lines = []
    for side in ("human", "auto"):
        s = (stats.get("sides") or {}).get(side) or {}
        if s.get("mean") is not None:
            lines.append(f"{side}: {s['mean']:.2f} ± {s['sem']:.2f}")
    return lines


def decide(meta, stats, claude, prepare_result):
    """Return (cell, verdict, source)."""
    status = meta.get("status")
    if status == "no_human_adapter":
        return "no human adapter", "no human adapter", "prepare"
    if status != "ok":
        reason = meta.get("reason") or f"the prepare job ended with {prepare_result or 'no result'}"
        return f"failed: {one_line(reason)}", "failed", "prepare"
    if not stats.get("available"):
        return f"failed: {one_line(stats.get('reason'))}", "failed", "stats"
    lines = score_lines(stats)
    if claude:
        verdict, cause, summary = claude
        source = "claude"
    else:
        verdict, cause = stats["rule_verdict"], None
        summary = "; ".join(stats["problems"][:2]) if verdict == "failed" else ""
        if verdict == "no match":
            cause, summary = "unclear", "Claude's analysis is missing"
        source = "rule"
    return "\n".join(lines + [verdict_line(verdict, cause, summary)]), verdict, source


def fmt(value):
    return "–" if value is None else f"{value:.2f}"


def report_md(env, meta, stats, cell, verdict, source, analysis):
    benchmark = meta.get("benchmark") or env.get("ADAPTER", "").removesuffix("-autoadapter")
    sha = (env.get("PR_SHA") or "")[:7]
    out = [f"# Comparison: {benchmark}", "",
           "| | |", "|---|---|",
           f"| Pull request | {env.get('PR_URL', '')} ({sha}) |",
           f"| Author | @{env.get('AUTHOR', '')} |",
           f"| Run | {env.get('RUN_URL', '')} |",
           f"| Harbor | {env.get('HARBOR_VERSION', '')} |",
           f"| Agent | {env.get('AGENT', '')} + {env.get('AGENT_MODEL', '')} |",
           f"| Time limits | task time limits x {env.get('TIMEOUT_MULTIPLIER', '')} |"]
    verifier_env = json.loads(env.get("VERIFIER_ENV") or "{}")
    if verifier_env:
        out.append("| Verifier env | " + ", ".join(f"{k}={v}" for k, v in verifier_env.items()) + " |")
    if meta.get("status") == "ok":
        out += [
            f"| Auto adapter | `{meta.get('auto_adapter')}`; dataset harbor-framework/harbor-datasets"
            f"#{meta.get('auto_dataset_pr')} `datasets/{meta.get('auto_dataset')}` at "
            f"{(meta.get('auto_dataset_commit') or '')[:12]} ({meta.get('auto_task_count')} tasks) |",
            f"| Human adapter | `{meta.get('human_adapter')}`; dataset `datasets/{meta.get('human_dataset')}` "
            f"at {(meta.get('human_dataset_commit') or '')[:12]} ({meta.get('human_task_count')} tasks) |",
            f"| Why this human adapter | {one_line(meta.get('human_adapter_reason'), 500)} |",
            f"| Compared problems | {meta.get('paired_count')} of {meta.get('paired_all_count')} paired: "
            f"human parity set from `{meta.get('parity_source')}` (human parity size {meta.get('parity_size')}) |",
            f"| Parity note | {one_line(meta.get('parity_note'), 500)} |",
        ]
    out += [f"| Verdict | {cell.splitlines()[-1]} (from {source}) |", ""]

    if stats.get("available"):
        runs = [f"run{i}" for i in range(1, stats["runs_expected"] + 1)]
        out += ["## Scores (percent of paired problems)", "",
                "| Side | " + " | ".join(runs) + " | kept mean ± SEM | kept range |",
                "|---|" + "---|" * (len(runs) + 2)]
        for side in ("human", "auto"):
            s = stats["sides"][side]
            cells = [fmt(s["runs"][r].get("score")) if s["runs"][r]["present"] else "missing" for r in runs]
            rng = f"{s['range'][0]:.2f} – {s['range'][1]:.2f}" if s["range"] else "–"
            ms = f"{s['mean']:.2f} ± {s['sem']:.2f}" if s["mean"] is not None else "–"
            out.append(f"| {side} | " + " | ".join(cells) + f" | {ms} | {rng} |")
        out += ["", f"Kept ranges overlap: {stats['overlap']}. Fixed rule says: {stats['rule_verdict']}.", ""]
        if stats["problems"]:
            out += ["Problems:", ""] + [f"- {p}" for p in stats["problems"]] + [""]
        errors = []
        for side in ("human", "auto"):
            for run, info in stats["sides"][side]["runs"].items():
                if info.get("exceptions"):
                    errors.append(f"- {side} {run}: " + ", ".join(
                        f"{k} x{v}" for k, v in sorted(info["exceptions"].items())))
        if errors:
            out += ["Exceptions per run:", ""] + errors + [""]
    elif meta.get("status") == "ok":
        out += [f"No scores: {stats.get('reason')}", ""]

    if meta.get("status") == "ok":
        out += ["## Pairing", "", f"Rule: {one_line(meta.get('pairing_rule'), 600)}", "",
                f"Notes: {one_line(meta.get('notes'), 1500)}", ""]
    elif meta.get("reason"):
        out += ["## Prepare", "", one_line(meta.get("reason"), 1500), ""]
    if analysis:
        out += ["## Claude's analysis", "", analysis.strip(), ""]
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stats", required=True)
    ap.add_argument("--compare", default="compare")
    ap.add_argument("--work", default="work")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    env = os.environ
    meta = load_json(Path(args.compare) / "meta.json") or {}
    stats = load_json(args.stats) or {"available": False, "reason": "stats.json is missing"}
    claude = claude_verdict(load_json(Path(args.work) / "verdict.json"))
    analysis_path = Path(args.work) / "analysis.md"
    analysis = analysis_path.read_text()[:20000] if analysis_path.is_file() else ""

    cell, verdict, source = decide(meta, stats, claude, env.get("PREPARE_RESULT"))
    benchmark = meta.get("benchmark") or env.get("ADAPTER", "").removesuffix("-autoadapter")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "REPORT.md").write_text(report_md(env, meta, stats, cell, verdict, source, analysis) + "\n")
    result = {"benchmark": benchmark, "author": env.get("AUTHOR", ""), "pr_url": env.get("PR_URL", ""),
              "cell": cell, "verdict": verdict, "verdict_source": source}
    (out / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(cell)
    return 0


if __name__ == "__main__":
    sys.exit(main())
