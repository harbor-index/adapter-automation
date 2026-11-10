"""Build oracle answers from BFCL ground truth.

BFCL ground truth lists, for every expected call, the accepted values of each
parameter (`""` in the list marks the parameter as optional). The oracle picks one
accepted value per required parameter and leaves optional parameters out.

Java and JavaScript tasks take parameter values as strings in that language's
syntax; the checker converts them back with the BFCL type converters. For those
tasks the oracle writes the value in Java/JavaScript syntax. Every oracle answer is
checked with the vendored BFCL checker when the task is generated.
"""

from __future__ import annotations

import itertools
import json
import math
from typing import Any

from .bfcl_upstream import is_java, is_js


# --- Python ----------------------------------------------------------------


def _pick_python(value_options: list, param_doc: dict) -> Any:
    """Pick the first accepted value; dict options hold nested option lists."""
    option = value_options[0]
    ptype = param_doc.get("type")
    if ptype == "dict" and isinstance(option, dict):
        return _pick_dict(option)
    if ptype in ("array", "tuple") and isinstance(option, list):
        items_type = (param_doc.get("items") or {}).get("type")
        if items_type == "dict":
            return [_pick_dict(d) if isinstance(d, dict) else d for d in option]
    return option


def _pick_dict(option: dict) -> dict:
    """Pick one value per key of a dict-valued ground truth option.

    Each key maps to a list of accepted values (`""` marks an optional key). The
    BFCL `dict_checker` tests `"" not in value` before it reports a missing key, so
    a key whose ground truth is a plain string (not a list) is optional as well.
    """
    out = {}
    for key, accepted in option.items():
        if isinstance(accepted, list):
            if "" in accepted:
                continue
            out[key] = accepted[0]
        elif isinstance(accepted, str):
            continue
        else:
            out[key] = accepted
    return out


# --- Java ------------------------------------------------------------------

_JAVA_ARRAY_ELEM = {
    "integer": "int",
    "int": "int",
    "long": "long",
    "short": "short",
    "byte": "byte",
    "float": "float",
    "double": "double",
    "boolean": "boolean",
    "char": "char",
    "String": "String",
}


def _java_scalar(value: Any, jtype: str | None) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        if jtype == "long":
            return f"{value}L"
        if jtype == "float":
            return f"{value}f"
        if jtype == "double":
            return f"{float(value)}"
        return str(value)
    if isinstance(value, float):
        if jtype == "float":
            return f"{value}f"
        return repr(value)
    return str(value)


def _java_value_untyped(value: Any) -> str:
    """Inverse of `parse_java_value` (used for HashMap values and untyped lists)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return '"' + str(value) + '"'


def _java_encode(value: Any, param_doc: dict) -> str:
    jtype = param_doc.get("type")
    if isinstance(value, str):
        return value
    if jtype == "Array" and isinstance(value, list):
        nested = (param_doc.get("items") or {}).get("type")
        elem = _JAVA_ARRAY_ELEM.get(nested or "", "Object")
        parts = [_java_scalar(v, nested) if nested else _java_value_untyped(v) for v in value]
        return f"new {elem}[]{{{', '.join(parts)}}}"
    if jtype == "ArrayList" and isinstance(value, list):
        nested = (param_doc.get("items") or {}).get("type")
        if not value:
            return "new ArrayList<>()"
        parts = []
        for v in value:
            if nested == "String":
                parts.append(f'"{v}"')
            elif nested == "char":
                parts.append(f"'{v}'")
            elif nested:
                parts.append(_java_scalar(v, nested))
            else:
                parts.append(_java_value_untyped(v))
        return f"new ArrayList<>(Arrays.asList({', '.join(parts)}))"
    if jtype == "HashMap" and isinstance(value, dict):
        puts = " ".join(f'put("{k}", {_java_value_untyped(v)});' for k, v in value.items())
        return f"new HashMap<String, Object>() {{{{ {puts} }}}}" if puts else "new HashMap<>()"
    return _java_scalar(value, jtype)


# --- JavaScript ------------------------------------------------------------


def _js_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_js_value(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{k}: {_js_value(v)}" for k, v in value.items()) + "}"
    return '"' + str(value) + '"'


def _js_encode(value: Any, param_doc: dict) -> str:
    jtype = param_doc.get("type")
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if jtype == "Bigint" and isinstance(value, int):
        return f"{value}n"
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return _js_value(value)


def _js_pick(value_options: list, param_doc: dict) -> Any:
    option = value_options[0]
    if param_doc.get("type") in ("dict", "HashMap") and isinstance(option, dict):
        return _pick_dict(option)
    return option


# --- Public API ------------------------------------------------------------


def _find_doc(functions: list[dict], name: str) -> dict | None:
    for f in functions:
        if f["name"] == name:
            return f
    return None


def _candidate_calls(test_category: str, functions: list[dict], ground_truth: list[dict]):
    """Yield candidate answers; the first one that the checker accepts is used."""
    per_call_candidates = []
    for expected in ground_truth:
        (name, params), = expected.items()
        doc = _find_doc(functions, name) or {"parameters": {"properties": {}}}
        props = doc["parameters"].get("properties", {})
        doc_required = set(doc["parameters"].get("required", []))
        required_params = []
        option_lists = []
        for pname, accepted in params.items():
            if "" in accepted and pname not in doc_required:
                continue  # optional: leave it out
            if pname in doc_required and accepted == [""]:
                # Required by the doc, but the only accepted value is the empty string.
                required_params.append(pname)
                option_lists.append([""])
                continue
            accepted = [a for a in accepted if a != ""]
            if not accepted:
                continue  # nothing to send; the checker decides
            pdoc = props.get(pname, {})
            # try every accepted value in order (only a few params have several)
            values = []
            for i in range(len(accepted)):
                rotated = accepted[i:] + accepted[:i]
                if is_java(test_category):
                    values.append(_java_encode(_js_pick(rotated, pdoc), pdoc))
                elif is_js(test_category):
                    values.append(_js_encode(_js_pick(rotated, pdoc), pdoc))
                else:
                    values.append(_pick_python(rotated, pdoc))
            required_params.append(pname)
            option_lists.append(values)
        combos = itertools.islice(itertools.product(*option_lists), 64)
        per_call_candidates.append(
            [{name: dict(zip(required_params, combo))} for combo in combos]
        )
    for answer in itertools.islice(itertools.product(*per_call_candidates), 256):
        yield list(answer)


def build_oracle_answer(test_category: str, functions: list[dict], ground_truth, checker) -> list[dict]:
    """Return an answer that `checker(answer) -> bool` accepts.

    `functions` are the raw (un-hinted) function docs that the BFCL checker uses.
    For relevance / irrelevance tasks, ground_truth is None.
    """
    if "irrelevance" in test_category:
        answer: list[dict] = []
        if not checker(answer):
            raise ValueError("oracle [] rejected for irrelevance task")
        return answer
    if "relevance" in test_category:
        # BFCL has no ground truth for relevance; it only checks that at least one
        # function call is made. Call the first function with no arguments.
        answer = [{functions[0]["name"]: {}}]
        if not checker(answer):
            raise ValueError("oracle call rejected for relevance task")
        return answer

    for answer in _candidate_calls(test_category, functions, ground_truth):
        if checker(answer):
            return json.loads(json.dumps(answer))
    raise ValueError("no oracle candidate passed the BFCL checker")
