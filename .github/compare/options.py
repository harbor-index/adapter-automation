#!/usr/bin/env python3
"""Read the settings that a maintainer wrote after the comparison command.

  options.py        (reads COMMENT_BODY and the workflow env, prints name=value lines)

Example comment:
  /compare-auto-with-human timeout-multiplier=20 model=deepseek-v4.1-flash concurrency=20

Only the first line of the comment is read. A setting that is not in the comment keeps
the value of the workflow env (TIMEOUT_MULTIPLIER, AGENT_MODEL, N_CONCURRENT). The
output lines go to GITHUB_OUTPUT of the check job.

verifier-env=NAME=value gives one variable to the verifier of each task, on both sides,
for example verifier-env=JUDGE_MODEL=gpt-5-mini. It can be given more than one time.
There is no default: without it, each task uses the values of its own task.toml.
"""
import json
import os
import re
import sys

COMMAND = "/compare-auto-with-human"
# setting in the comment -> (output name, workflow env default, pattern of a correct value)
SETTINGS = {
    "timeout-multiplier": ("timeout_multiplier", "TIMEOUT_MULTIPLIER", r"[0-9]+(\.[0-9]+)?"),
    "model": ("agent_model", "AGENT_MODEL", r"[A-Za-z0-9][A-Za-z0-9._/-]*"),
    "concurrency": ("n_concurrent", "N_CONCURRENT", r"[0-9]+"),
}
VERIFIER_ENV = "verifier-env"
VALUE = r"[A-Za-z0-9][A-Za-z0-9._/:@+-]*"   # a value of a verifier variable: no spaces, no quotes


def parse(body, env):
    """Return {output name: value} for the comment text and the workflow env."""
    lines = body.strip().splitlines()
    words = lines[0].split() if lines else []
    if not words or words[0] != COMMAND:
        raise ValueError(f"the comment must start with {COMMAND}")
    given, verifier_env = {}, {}
    for word in words[1:]:
        key, sep, value = word.partition("=")
        if key == VERIFIER_ENV and sep:
            name, sep, value = value.partition("=")
            if not sep or not re.fullmatch(r"[A-Z][A-Z0-9_]*", name) or not re.fullmatch(VALUE, value):
                raise ValueError(f"bad value for {VERIFIER_ENV}: {word} (use {VERIFIER_ENV}=NAME=value)")
            if name in verifier_env:
                raise ValueError(f"variable given two times: {name}")
            verifier_env[name] = value
            continue
        if not sep or key not in SETTINGS:
            raise ValueError(f"unknown setting: {word} (known: {', '.join([*SETTINGS, VERIFIER_ENV])})")
        if key in given:
            raise ValueError(f"setting given two times: {key}")
        if not re.fullmatch(SETTINGS[key][2], value) or (key != "model" and float(value) <= 0):
            raise ValueError(f"bad value for {key}: {value}")
        given[key] = value
    values = {name: given.get(key, env[default]) for key, (name, default, _) in SETTINGS.items()}
    values["verifier_env"] = json.dumps(verifier_env)
    return values


def main():
    try:
        values = parse(os.environ.get("COMMENT_BODY", ""), os.environ)
    except ValueError as error:
        sys.exit(f"Cannot read the settings: {error}")
    for name, value in values.items():
        print(f"{name}={value}")


if __name__ == "__main__":
    main()
