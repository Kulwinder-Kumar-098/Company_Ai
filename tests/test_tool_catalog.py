import json

from app.agents.tool_catalog import (
    build_tool_catalog,
    compact_tool_catalog_json,
    planner_fingerprint,
)
from app.tools.registry import TOOLS


def test_compact_catalog_preserves_registered_argument_names_and_requiredness():
    catalog = {tool["name"]: tool for tool in build_tool_catalog()}

    assert set(catalog) == set(TOOLS)
    for name, definition in TOOLS.items():
        schema = definition.input_schema.model_json_schema()
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))
        arguments = catalog[name]["arguments"]

        assert set(arguments) == set(properties)
        assert {
            argument
            for argument, metadata in arguments.items()
            if metadata[1]
        } == required


def test_compact_catalog_is_at_least_half_the_size_of_full_schemas():
    old_catalog = [
        {
            "name": name,
            "description": definition.description,
            "arguments_schema": definition.input_schema.model_json_schema(),
        }
        for name, definition in TOOLS.items()
    ]
    old_json = json.dumps(old_catalog, ensure_ascii=False)
    compact_json = compact_tool_catalog_json()

    assert len(compact_json) <= len(old_json) * 0.5


def test_compact_catalog_and_fingerprint_are_deterministic():
    assert compact_tool_catalog_json() == compact_tool_catalog_json()
    assert planner_fingerprint() == planner_fingerprint()
