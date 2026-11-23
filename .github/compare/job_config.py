#!/usr/bin/env python3
"""Print the Harbor JobConfig (JSON) for one run of the comparison.

  job_config.py --side auto|human --run run1..runN --pr <number>

Settings come from the workflow env: AGENT, AGENT_MODEL, AGENT_MAX_TOKENS,
TIMEOUT_MULTIPLIER, N_CONCURRENT, MAX_RETRIES and OPENAI_BASE_URL. The agent reads
OPENAI_API_KEY itself; no key is written into the config. AGENT_MODEL is the name that
the model gateway uses. The gateway has the OpenAI API, so the agent's LiteLLM client
gets the name with the prefix openai/. VERIFIER_ENV (a JSON object, can be empty) holds
variables for the verifier of each task; they replace the values of the task.toml.
"""
import argparse
import json
import os
import re
import sys


def api_base(url):
    """The OpenAI-style base URL with /v1 at the end, as the agent's LiteLLM client needs it."""
    url = url.strip().rstrip("/")
    return url if url.endswith("/v1") else url + "/v1"


def build(side, run, pr, env):
    if side not in ("auto", "human"):
        raise ValueError(f"bad side: {side}")
    if not re.fullmatch(r"run[0-9]+", run):
        raise ValueError(f"bad run: {run}")
    agent = {
        "name": env["AGENT"],
        "model_name": "openai/" + env["AGENT_MODEL"],
        "kwargs": {
            "api_base": api_base(env["OPENAI_BASE_URL"]),
            "llm_call_kwargs": {"max_tokens": int(env["AGENT_MAX_TOKENS"])},
        },
    }
    config = {
        "job_name": f"{side}-{run}",
        "jobs_dir": "jobs",
        "n_concurrent_trials": int(env["N_CONCURRENT"]),
        "timeout_multiplier": float(env["TIMEOUT_MULTIPLIER"]),
        "retry": {"max_retries": int(env["MAX_RETRIES"])},
        "environment": {
            "type": "daytona",
            "kwargs": {"labels": {
                "compare_owner": "compare-auto-with-human",
                "compare_job": f"pr{pr}-{side}-{run}",
            }},
        },
        "agents": [agent],
        "datasets": [{"path": f"compare/{side}"}],
    }
    verifier_env = json.loads(env.get("VERIFIER_ENV") or "{}")
    if verifier_env:
        config["verifier"] = {"env": verifier_env}
    return config


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--side", required=True)
    ap.add_argument("--run", required=True)
    ap.add_argument("--pr", required=True, type=int)
    args = ap.parse_args()
    json.dump(build(args.side, args.run, args.pr, os.environ), sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
