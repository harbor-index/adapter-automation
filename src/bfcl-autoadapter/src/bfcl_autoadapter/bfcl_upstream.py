"""Small pieces of the original BFCL code that the adapter and the parity agent reuse.

Copied from https://github.com/ShishirPatil/gorilla at commit
6ea57973c7a6097fd7c5915698c54c17c5b1b6c8 (Apache License 2.0), directory
`berkeley-function-call-leaderboard/bfcl_eval/`. The logic is unchanged; only the
imports and a few module-level names are adapted so that this file has no
dependency on the `bfcl_eval` package.
"""

from __future__ import annotations

import copy
import json
import re

# --- bfcl_eval/constants/type_mappings.py ------------------------------------

GORILLA_TO_OPENAPI = {
    "integer": "integer",
    "number": "number",
    "float": "number",
    "string": "string",
    "boolean": "boolean",
    "bool": "boolean",
    "array": "array",
    "list": "array",
    "dict": "object",
    "object": "object",
    "tuple": "array",
    "any": "string",
    "byte": "integer",
    "short": "integer",
    "long": "integer",
    "double": "number",
    "char": "string",
    "ArrayList": "array",
    "Array": "array",
    "HashMap": "object",
    "Hashtable": "object",
    "Queue": "array",
    "Stack": "array",
    "Any": "string",
    "String": "string",
    "Bigint": "integer",
}


# --- bfcl_eval/utils.py: category helpers ------------------------------------


def is_java(test_category: str) -> bool:
    return "java" in test_category and not is_js(test_category)


def is_js(test_category: str) -> bool:
    return "javascript" in test_category


def is_relevance_or_irrelevance(test_category: str) -> bool:
    return "relevance" in test_category or "irrelevance" in test_category


# --- bfcl_eval/utils.py: language-specific hints -----------------------------
# Applied to the function docs that the model sees at generation time
# (`load_dataset_entry(..., include_language_specific_hint=True)`).


def _get_language_specific_hint(test_category):
    if is_java(test_category):
        return " Note that the provided function is in Java 8 SDK syntax."
    elif is_js(test_category):
        return " Note that the provided function is in JavaScript syntax."
    else:
        return " Note that the provided function is in Python 3 syntax."


def _func_doc_language_specific_pre_processing(
    function: list[dict], test_category: str
) -> list[dict]:
    if len(function) == 0:
        return function

    assert type(function) == list
    for item in function:
        # Add language specific hints to the function description
        item["description"] = item["description"] + _get_language_specific_hint(
            test_category
        )
        # Process the parameters
        properties = item["parameters"]["properties"]
        if is_java(test_category):
            for key, value in properties.items():
                if value["type"] == "any":
                    properties[key][
                        "description"
                    ] += " This parameter can be of any type of Java object in string representation."
                else:
                    value[
                        "description"
                    ] += f" This is Java {value['type']} type parameter in string representation."
                if value["type"] == "ArrayList" or value["type"] == "Array":
                    value[
                        "description"
                    ] += f" The list elements are of type {value['items']['type']}; they are not in string representation."
                    del value["items"]

                value["type"] = "string"

        elif is_js(test_category):
            for key, value in properties.items():
                if value["type"] == "any":
                    properties[key][
                        "description"
                    ] += " This parameter can be of any type of JavaScript object in string representation."
                else:
                    value[
                        "description"
                    ] += f" This is JavaScript {value['type']} type parameter in string representation."
                if value["type"] == "array":
                    value[
                        "description"
                    ] += f" The list elements are of type {value['items']['type']}; they are not in string representation."
                    del value["items"]

                if value["type"] == "dict":
                    if "properties" in value:  # not every dict has properties
                        value[
                            "description"
                        ] += f" The dictionary entries have the following schema; they are not in string representation. {json.dumps(value['properties'])}"
                        del value["properties"]

                value["type"] = "string"

    return function


def add_language_specific_hint(functions: list[dict], test_category: str) -> list[dict]:
    """Return a hinted deep copy of `functions` (the input is not modified)."""
    return _func_doc_language_specific_pre_processing(
        copy.deepcopy(functions), test_category
    )


# --- bfcl_eval/model_handler/utils.py: function doc -> OpenAI tool -----------
# `convert_to_tool(functions, GORILLA_TO_OPENAPI, ModelStyle.OPENAI_COMPLETIONS)`,
# reduced to the branches that run for the OpenAI chat-completions model style.


def _cast_to_openai_type(properties, mapping):
    for key, value in properties.items():
        if "type" not in value:
            properties[key]["type"] = "string"
        else:
            var_type = value["type"]
            if mapping == GORILLA_TO_OPENAPI and var_type == "float":
                properties[key]["format"] = "float"
                properties[key]["description"] += " This is a float type value."
            if var_type in mapping:
                properties[key]["type"] = mapping[var_type]
            else:
                properties[key]["type"] = "string"

        # Currently support:
        # - list of any
        # - list of list of any
        # - list of dict
        # - list of list of dict
        # - dict of any

        if properties[key]["type"] == "array" or properties[key]["type"] == "object":
            if "properties" in properties[key]:
                properties[key]["properties"] = _cast_to_openai_type(
                    properties[key]["properties"], mapping
                )
            elif "items" in properties[key]:
                properties[key]["items"]["type"] = mapping[properties[key]["items"]["type"]]
                if (
                    properties[key]["items"]["type"] == "array"
                    and "items" in properties[key]["items"]
                ):
                    properties[key]["items"]["items"]["type"] = mapping[
                        properties[key]["items"]["items"]["type"]
                    ]
                elif (
                    properties[key]["items"]["type"] == "object"
                    and "properties" in properties[key]["items"]
                ):
                    properties[key]["items"]["properties"] = _cast_to_openai_type(
                        properties[key]["items"]["properties"], mapping
                    )
    return properties


def convert_to_openai_tools(functions: list[dict]) -> list[dict]:
    functions = copy.deepcopy(functions)
    oai_tool = []
    for item in functions:
        if "." in item["name"]:
            # OAI does not support "." in the function name so we replace it with "_". ^[a-zA-Z0-9_-]{1,64}$ is the regex for the name.
            item["name"] = re.sub(r"\.", "_", item["name"])

        item["parameters"]["type"] = "object"
        item["parameters"]["properties"] = _cast_to_openai_type(
            item["parameters"]["properties"], GORILLA_TO_OPENAPI
        )
        # ModelStyle.OPENAI_COMPLETIONS
        oai_tool.append({"type": "function", "function": item})
    return oai_tool
