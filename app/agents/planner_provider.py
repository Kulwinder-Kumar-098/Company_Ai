"""Provider adapters for structured planner responses."""

import json
from copy import deepcopy
from typing import Any

from groq import (
    APIError,
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    Groq,
    RateLimitError,
)
from pydantic import BaseModel

from app.tools.registry import TOOLS


class PlannerProviderError(RuntimeError):
    """A provider failure with a safe, stable telemetry category."""

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


def _strict_value_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Keep only the JSON Schema subset supported by Groq strict mode."""
    if "anyOf" in schema:
        variants = [
            _strict_value_schema(variant)
            for variant in schema["anyOf"]
            if variant.get("type") != "null"
        ]
        if len(variants) == 1:
            return variants[0]
        return {"anyOf": variants}
    if schema.get("type") == "array":
        return {
            "type": "array",
            "items": _strict_value_schema(schema["items"]),
        }
    if schema.get("type") == "object":
        return _strict_object_schema(schema)
    result: dict[str, Any] = {}
    if "type" in schema:
        result["type"] = schema["type"]
    if "enum" in schema:
        result["enum"] = schema["enum"]
    return result


def _strict_object_schema(schema: dict[str, Any]) -> dict[str, Any]:
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))
    strict_properties: dict[str, Any] = {}
    for name, value in properties.items():
        converted = _strict_value_schema(value)
        if name not in required:
            converted = {"anyOf": [converted, {"type": "null"}]}
        strict_properties[name] = converted
    return {
        "type": "object",
        "properties": strict_properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def groq_request_plan_schema() -> dict[str, Any]:
    """Build a strict schema from the registered Pydantic tool schemas."""
    tool_variants = []
    for name, definition in TOOLS.items():
        argument_schema = _strict_object_schema(
            definition.input_schema.model_json_schema()
        )
        tool_variants.append(
            {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "enum": [name]},
                    "arguments": argument_schema,
                },
                "required": ["name", "arguments"],
                "additionalProperties": False,
            }
        )
    return {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": ["rag", "structured", "mixed", "unsupported"],
            },
            "tool_calls": {
                "type": "array",
                "items": {"anyOf": tool_variants},
            },
        },
        "required": ["mode", "tool_calls"],
        "additionalProperties": False,
    }


def normalize_groq_plan_arguments(payload: dict[str, Any]) -> dict[str, Any]:
    """Remove nulls for optional fields so Pydantic applies their defaults."""
    normalized = deepcopy(payload)
    tool_calls = normalized.get("tool_calls")
    if not isinstance(tool_calls, list):
        return normalized
    for tool_call in tool_calls:
        if not isinstance(tool_call, dict):
            continue
        name = tool_call.get("name")
        arguments = tool_call.get("arguments")
        definition = TOOLS.get(name) if isinstance(name, str) else None
        if definition is None or not isinstance(arguments, dict):
            continue
        required = set(
            definition.input_schema.model_json_schema().get("required", [])
        )
        for key in list(arguments):
            if arguments[key] is None and key not in required:
                del arguments[key]
    return normalized


def request_groq_json(
    *,
    system_prompt: str,
    user_prompt: str,
    model: str,
    api_key: str | None,
    timeout: float,
) -> str:
    if not api_key or not api_key.strip():
        raise PlannerProviderError("provider_misconfigured")
    try:
        client = Groq(
            api_key=api_key,
            timeout=timeout,
            max_retries=1,
        )
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "company_ai_request_plan",
                    "strict": True,
                    "schema": groq_request_plan_schema(),
                },
            },
            temperature=0,
            max_completion_tokens=2048,
        )
    except AuthenticationError as exc:
        raise PlannerProviderError("provider_authentication") from exc
    except RateLimitError as exc:
        raise PlannerProviderError("provider_rate_limit") from exc
    except APITimeoutError as exc:
        raise PlannerProviderError("provider_timeout") from exc
    except APIConnectionError as exc:
        raise PlannerProviderError("provider_unavailable") from exc
    except BadRequestError as exc:
        raise PlannerProviderError("provider_bad_request") from exc
    except APIStatusError as exc:
        raise PlannerProviderError("provider_error") from exc
    except APIError as exc:
        raise PlannerProviderError("provider_error") from exc

    if not response.choices:
        raise PlannerProviderError("invalid_model_output")
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError, TypeError) as exc:
        raise PlannerProviderError("invalid_model_output") from exc
    if not isinstance(content, str) or not content.strip():
        raise PlannerProviderError("invalid_model_output")
    return content.strip()


def request_planner_json(
    *,
    provider: str,
    system_prompt: str,
    user_prompt: str,
    model: str,
    api_key: str | None,
    timeout: float,
    output_schema: type[BaseModel],
    ollama_client: Any,
) -> str:
    """Request a planner response through the configured provider adapter."""
    if provider == "groq":
        raw = request_groq_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=model,
            api_key=api_key,
            timeout=timeout,
        )
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise PlannerProviderError("invalid_model_json") from exc
        if not isinstance(payload, dict):
            raise PlannerProviderError("invalid_model_output")
        return json.dumps(normalize_groq_plan_arguments(payload))
    if provider != "ollama":
        raise PlannerProviderError("provider_misconfigured")

    response = ollama_client.chat(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        format=output_schema.model_json_schema(),
        options={"temperature": 0.0},
    )
    if isinstance(response, dict):
        message = response.get("message")
        content = message.get("content") if isinstance(message, dict) else None
    else:
        message = getattr(response, "message", None)
        content = getattr(message, "content", None)
    if not isinstance(content, str) or not content.strip():
        raise PlannerProviderError("invalid_model_output")
    return content.strip()
