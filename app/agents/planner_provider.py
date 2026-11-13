"""Provider adapters for structured planner responses."""

import json
import logging
from copy import deepcopy
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
from threading import Lock
import time
from typing import Any

import httpx
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
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from pydantic import BaseModel

from app.core.config import settings
from app.tools.registry import TOOLS

logger = logging.getLogger(__name__)


class PlannerProviderError(RuntimeError):
    """A provider failure with a safe, stable telemetry category."""

    def __init__(
        self,
        category: str,
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> None:
        super().__init__(category)
        self.category = category
        self.provider = provider
        self.model = model


class ProviderCooldowns:
    """Thread-safe per-provider cooldown state with an injectable clock."""

    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._lock = Lock()
        self._blocked_until: dict[str, float] = {}

    def block(self, provider: str, seconds: float) -> None:
        bounded_seconds = max(1.0, min(seconds, 600.0))
        with self._lock:
            self._blocked_until[provider] = self._clock() + bounded_seconds

    def is_blocked(self, provider: str) -> bool:
        now = self._clock()
        with self._lock:
            blocked_until = self._blocked_until.get(provider)
            if blocked_until is None:
                return False
            if blocked_until <= now:
                del self._blocked_until[provider]
                return False
            return True


provider_cooldowns = ProviderCooldowns()


def _retry_after_seconds(response: object) -> float | None:
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    value = headers.get("Retry-After")
    if not isinstance(value, str):
        return None
    try:
        seconds = float(value)
        return seconds if math.isfinite(seconds) else None
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return (retry_at - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None


def _record_rate_limit(provider: str, response: object) -> None:
    if not settings.planner_rate_limit_cooldown_enabled:
        return
    delay = _retry_after_seconds(response)
    if delay is None:
        delay = settings.planner_rate_limit_cooldown_seconds
    else:
        delay = max(1.0, min(delay, 300.0))
    provider_cooldowns.block(provider, delay)


def _provider_is_cooling_down(provider: str) -> bool:
    return (
        settings.planner_rate_limit_cooldown_enabled
        and provider_cooldowns.is_blocked(provider)
    )


def _token_count(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _log_token_usage(
    provider: str,
    model: str,
    usage: object,
    *,
    prompt_field: str,
    completion_field: str,
    total_field: str,
) -> None:
    if not settings.planner_token_usage_logging_enabled:
        return
    logger.info(
        "planner_token_usage %s",
        json.dumps(
            {
                "provider": provider,
                "model": model,
                "prompt_tokens": _token_count(
                    getattr(usage, prompt_field, None)
                ),
                "completion_tokens": _token_count(
                    getattr(usage, completion_field, None)
                ),
                "total_tokens": _token_count(
                    getattr(usage, total_field, None)
                ),
            },
            sort_keys=True,
        ),
    )


def _gemini_response_schema(schema: dict[str, Any]) -> genai_types.Schema:
    """Convert Pydantic JSON Schema to the Gemini Developer API subset."""
    definitions = schema.get("$defs", {})

    def convert(value: dict[str, Any]) -> dict[str, Any]:
        reference = value.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/$defs/"):
            name = reference.rsplit("/", 1)[-1]
            definition = definitions.get(name)
            if not isinstance(definition, dict):
                raise PlannerProviderError(
                    "provider_bad_request",
                    provider="gemini",
                )
            return convert(definition)

        result: dict[str, Any] = {}
        schema_type = value.get("type")
        if isinstance(schema_type, str):
            result["type"] = schema_type.upper()
        if isinstance(value.get("enum"), list):
            result["enum"] = value["enum"]
        if isinstance(value.get("required"), list):
            result["required"] = value["required"]
        properties = value.get("properties")
        if isinstance(properties, dict):
            result["properties"] = {
                name: convert(child)
                for name, child in properties.items()
                if isinstance(name, str) and isinstance(child, dict)
            }
        items = value.get("items")
        if isinstance(items, dict):
            result["items"] = convert(items)
        return result

    return genai_types.Schema.model_validate(convert(schema))


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
        raise PlannerProviderError(
            "provider_misconfigured",
            provider="groq",
            model=model,
        )
    try:
        client = Groq(
            api_key=api_key,
            timeout=timeout,
            max_retries=0,
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
        _log_token_usage(
            "groq",
            model,
            getattr(response, "usage", None),
            prompt_field="prompt_tokens",
            completion_field="completion_tokens",
            total_field="total_tokens",
        )
    except AuthenticationError as exc:
        raise PlannerProviderError(
            "provider_authentication",
            provider="groq",
            model=model,
        ) from exc
    except RateLimitError as exc:
        _record_rate_limit("groq", exc.response)
        raise PlannerProviderError(
            "provider_rate_limit",
            provider="groq",
            model=model,
        ) from exc
    except APITimeoutError as exc:
        raise PlannerProviderError(
            "provider_timeout",
            provider="groq",
            model=model,
        ) from exc
    except APIConnectionError as exc:
        raise PlannerProviderError(
            "provider_unavailable",
            provider="groq",
            model=model,
        ) from exc
    except BadRequestError as exc:
        raise PlannerProviderError(
            "provider_bad_request",
            provider="groq",
            model=model,
        ) from exc
    except APIStatusError as exc:
        raise PlannerProviderError(
            "provider_error",
            provider="groq",
            model=model,
        ) from exc
    except APIError as exc:
        raise PlannerProviderError(
            "provider_error",
            provider="groq",
            model=model,
        ) from exc

    if not response.choices:
        raise PlannerProviderError(
            "invalid_model_output",
            provider="groq",
            model=model,
        )
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError, TypeError) as exc:
        raise PlannerProviderError(
            "invalid_model_output",
            provider="groq",
            model=model,
        ) from exc
    if not isinstance(content, str) or not content.strip():
        raise PlannerProviderError(
            "invalid_model_output",
            provider="groq",
            model=model,
        )
    return content.strip()


def request_gemini_json(
    *,
    system_prompt: str,
    user_prompt: str,
    model: str,
    api_key: str | None,
    timeout: float,
    output_schema: type[BaseModel],
) -> str:
    if not api_key or not api_key.strip():
        raise PlannerProviderError("provider_misconfigured")
    try:
        client = genai.Client(
            api_key=api_key,
            http_options=genai_types.HttpOptions(timeout=int(timeout * 1000)),
        )
        response = client.models.generate_content(
            model=model,
            contents=user_prompt,
            config=genai_types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                response_schema=_gemini_response_schema(
                    output_schema.model_json_schema()
                ),
                temperature=0,
            ),
        )
        _log_token_usage(
            "gemini",
            model,
            getattr(response, "usage_metadata", None),
            prompt_field="prompt_token_count",
            completion_field="candidates_token_count",
            total_field="total_token_count",
        )
        content = response.text
    except genai_errors.APIError as exc:
        if exc.code == 429:
            category = "provider_rate_limit"
            _record_rate_limit("gemini", exc.response)
        elif exc.code in {401, 403}:
            category = "provider_authentication"
        elif exc.code == 400:
            category = "provider_bad_request"
        elif exc.code in {500, 502, 503, 504}:
            category = "provider_unavailable"
        else:
            category = "provider_error"
        raise PlannerProviderError(
            category,
            provider="gemini",
            model=model,
        ) from exc
    except (httpx.TimeoutException, TimeoutError) as exc:
        raise PlannerProviderError(
            "provider_timeout",
            provider="gemini",
            model=model,
        ) from exc
    except (httpx.TransportError, ConnectionError) as exc:
        raise PlannerProviderError(
            "provider_unavailable",
            provider="gemini",
            model=model,
        ) from exc
    except (AttributeError, TypeError, ValueError) as exc:
        raise PlannerProviderError(
            "invalid_model_output",
            provider="gemini",
            model=model,
        ) from exc

    if not isinstance(content, str) or not content.strip():
        raise PlannerProviderError(
            "invalid_model_output",
            provider="gemini",
            model=model,
        )
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
    fallback_api_key: str | None = None,
    fallback_model: str | None = None,
    include_provider: bool = False,
) -> str | tuple[str, str, str]:
    """Request a planner response through the configured provider adapter."""
    selected_provider = provider
    selected_model = model
    if provider == "groq":
        use_gemini = _provider_is_cooling_down("groq")
        if use_gemini and (
            not fallback_api_key or not fallback_api_key.strip()
        ):
            raise PlannerProviderError(
                "provider_rate_limit",
                provider="groq",
                model=model,
            )
        if not use_gemini:
            try:
                raw = request_groq_json(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    model=model,
                    api_key=api_key,
                    timeout=timeout,
                )
            except PlannerProviderError as exc:
                if (
                    not fallback_api_key
                    or not fallback_api_key.strip()
                    or exc.category not in {
                        "provider_rate_limit",
                        "provider_timeout",
                        "provider_unavailable",
                        "provider_error",
                    }
                ):
                    raise
                use_gemini = True
        if use_gemini:
            selected_provider = "gemini"
            selected_model = fallback_model or "gemini-3.8-flash"
            if _provider_is_cooling_down("gemini"):
                raise PlannerProviderError(
                    "provider_rate_limit",
                    provider="gemini",
                    model=selected_model,
                )
            raw = request_gemini_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                model=selected_model,
                api_key=fallback_api_key,
                timeout=timeout,
                output_schema=output_schema,
            )
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise PlannerProviderError(
                "invalid_model_json",
                provider=selected_provider,
                model=selected_model,
            ) from exc
        if not isinstance(payload, dict):
            raise PlannerProviderError(
                "invalid_model_output",
                provider=selected_provider,
                model=selected_model,
            )
        result = json.dumps(normalize_groq_plan_arguments(payload))
        if include_provider:
            return result, selected_provider, selected_model
        return result
    if provider != "ollama":
        raise PlannerProviderError("provider_misconfigured")

    response = ollama_client.chat(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        format=output_schema.model_json_schema(),
        options={
            "temperature": 0.0,
            "num_ctx": settings.ollama_num_ctx,
        },
        keep_alive=settings.ollama_keep_alive,
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
