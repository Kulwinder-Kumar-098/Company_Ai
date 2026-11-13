"""Compact, deterministic catalog of registered planner tools."""

import hashlib
import json
from typing import Any

from pydantic import BaseModel

from app.agents.prompts import PLANNER_SYSTEM_PROMPT
from app.tools.registry import TOOLS


def _resolve_schema(
    schema: dict[str, Any],
    definitions: dict[str, Any],
) -> dict[str, Any]:
    reference = schema.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/$defs/"):
        definition = definitions.get(reference.rsplit("/", 1)[-1])
        if isinstance(definition, dict):
            return _resolve_schema(definition, definitions)

    variants = schema.get("anyOf")
    if isinstance(variants, list):
        non_null = [
            variant
            for variant in variants
            if isinstance(variant, dict) and variant.get("type") != "null"
        ]
        if len(non_null) == 1:
            return _resolve_schema(non_null[0], definitions)

    return schema


def compact_arguments_schema(
    input_schema: type[BaseModel],
) -> dict[str, list[object]]:
    schema = input_schema.model_json_schema()
    definitions = schema.get("$defs", {})
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))
    compact: dict[str, list[object]] = {}

    for name, property_schema in properties.items():
        resolved = _resolve_schema(property_schema, definitions)
        property_type = resolved.get("type", "object")
        compact_property: list[object] = [
            property_type,
            name in required,
        ]
        enum_values = resolved.get("enum")
        if isinstance(enum_values, list):
            compact_property.append(enum_values)
        compact[name] = compact_property

    return compact


def build_tool_catalog() -> list[dict[str, object]]:
    return [
        {
            "name": name,
            "description": definition.description,
            "arguments": compact_arguments_schema(definition.input_schema),
        }
        for name, definition in TOOLS.items()
    ]


def compact_tool_catalog_json() -> str:
    return json.dumps(
        build_tool_catalog(),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def planner_fingerprint() -> str:
    digest = hashlib.sha256()
    digest.update(PLANNER_SYSTEM_PROMPT.encode("utf-8"))
    digest.update(compact_tool_catalog_json().encode("utf-8"))
    return digest.hexdigest()
