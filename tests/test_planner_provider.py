"""Provider selection and strict structured-output tests."""

import json
import logging
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr
from pydantic import ValidationError

from app.agents import orchestrator, planner_provider
from app.agents.prompts import PLANNER_SYSTEM_PROMPT
from app.agents.workflows import RequestPlan
from app.core.config import Settings, settings
from app.evaluation import runner
from app.evaluation.runner import fixture_predictor, load_dataset
from app.observability.metrics import AgentTelemetryAggregator


def _groq_response(content: str):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
            )
        ]
    )


def test_groq_request_uses_strict_schema_and_registered_tools(monkeypatch):
    captured = {}
    plan_json = json.dumps(
        {
            "mode": "structured",
            "tool_calls": [
                {
                    "name": "get_project_tasks",
                    "arguments": {
                        "project_id": "P-204",
                        "limit": None,
                        "offset": 0,
                    },
                }
            ],
        }
    )

    class FakeGroq:
        def __init__(self, **kwargs):
            captured["client"] = kwargs
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=self.create)
            )

        def create(self, **kwargs):
            captured["request"] = kwargs
            return _groq_response(plan_json)

    monkeypatch.setattr(planner_provider, "Groq", FakeGroq)
    result = planner_provider.request_groq_json(
        system_prompt=PLANNER_SYSTEM_PROMPT,
        user_prompt='{"question":"synthetic"}',
        model="openai/gpt-oss-20b",
        api_key="test-key-never-logged",
        timeout=12,
    )

    request = captured["request"]
    assert captured["client"]["max_retries"] == 0
    assert captured["client"]["timeout"] == 12
    assert request["model"] == "openai/gpt-oss-20b"
    assert request["temperature"] == 0
    assert request["response_format"]["type"] == "json_schema"
    assert request["response_format"]["json_schema"]["strict"] is True
    assert result == plan_json

    schema = request["response_format"]["json_schema"]["schema"]
    assert schema["required"] == ["mode", "tool_calls"]
    assert schema["additionalProperties"] is False
    assert all(
        variant["properties"]["name"]["enum"][0]
        for variant in schema["properties"]["tool_calls"]["items"]["anyOf"]
    )
    arguments = planner_provider.normalize_groq_plan_arguments(
        json.loads(result)
    )["tool_calls"][0]["arguments"]
    assert arguments == {"project_id": "P-204", "offset": 0}


def test_groq_schema_requires_all_properties_and_forbids_extra_fields():
    schema = planner_provider.groq_request_plan_schema()

    def check_objects(value):
        if isinstance(value, dict):
            if value.get("type") == "object":
                assert value["additionalProperties"] is False
                assert set(value["required"]) == set(value["properties"])
            for child in value.values():
                check_objects(child)
        elif isinstance(value, list):
            for child in value:
                check_objects(child)

    check_objects(schema)


def test_provider_interface_dispatches_groq_structured_plan(monkeypatch):
    class FakeGroq:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **_params: _groq_response(
                        '{"mode":"structured","tool_calls":[{"name":"get_project_tasks","arguments":{"project_id":"P-204","limit":null,"offset":0}}]}'
                    )
                )
            )

    monkeypatch.setattr(planner_provider, "Groq", FakeGroq)
    result = planner_provider.request_planner_json(
        provider="groq",
        system_prompt="synthetic",
        user_prompt="synthetic",
        model="openai/gpt-oss-20b",
        api_key="test-key",
        timeout=1,
        output_schema=RequestPlan,
        ollama_client=object(),
    )

    assert json.loads(result)["tool_calls"][0]["arguments"] == {
        "project_id": "P-204",
        "offset": 0,
    }


def test_groq_rate_limit_falls_back_to_gemini_with_provider_metadata(
    monkeypatch,
):
    calls = []

    def groq_rate_limit(**_kwargs):
        raise planner_provider.PlannerProviderError("provider_rate_limit")

    def gemini_response(**kwargs):
        calls.append(kwargs)
        return '{"mode":"unsupported","tool_calls":[]}'

    monkeypatch.setattr(planner_provider, "request_groq_json", groq_rate_limit)
    monkeypatch.setattr(planner_provider, "request_gemini_json", gemini_response)
    result = planner_provider.request_planner_json(
        provider="groq",
        system_prompt="synthetic system",
        user_prompt="synthetic user",
        model="openai/gpt-oss-20b",
        api_key="groq-test-key",
        timeout=1,
        output_schema=RequestPlan,
        ollama_client=object(),
        fallback_api_key="gemini-test-key",
        fallback_model="gemini-test-model",
        include_provider=True,
    )

    assert isinstance(result, tuple)
    assert json.loads(result[0]) == {
        "mode": "unsupported",
        "tool_calls": [],
    }
    assert result[1:] == ("gemini", "gemini-test-model")
    assert calls[0]["api_key"] == "gemini-test-key"
    assert calls[0]["model"] == "gemini-test-model"


def test_groq_http_429_falls_back_to_gemini(monkeypatch):
    cooldowns = planner_provider.ProviderCooldowns(clock=lambda: 10.0)
    monkeypatch.setattr(planner_provider, "provider_cooldowns", cooldowns)
    monkeypatch.setattr(settings, "planner_rate_limit_cooldown_enabled", True)
    groq_response = httpx.Response(
        429,
        headers={"Retry-After": "120"},
        request=httpx.Request("POST", "https://api.groq.com"),
    )
    rate_limit = planner_provider.RateLimitError(
        "rate limit",
        response=groq_response,
        body=None,
    )
    groq_calls = []
    gemini_calls = []

    def groq_completion(**_params):
        groq_calls.append(True)
        raise rate_limit

    class FakeGroq:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=groq_completion)
            )

    def gemini_response(**kwargs):
        gemini_calls.append(kwargs)
        return '{"mode":"unsupported","tool_calls":[]}'

    monkeypatch.setattr(planner_provider, "Groq", FakeGroq)
    monkeypatch.setattr(
        planner_provider,
        "request_gemini_json",
        gemini_response,
    )

    result = planner_provider.request_planner_json(
        provider="groq",
        system_prompt="synthetic system",
        user_prompt="synthetic user",
        model="groq-test-model",
        api_key="groq-test-key",
        timeout=1,
        output_schema=RequestPlan,
        ollama_client=object(),
        fallback_api_key="gemini-test-key",
        fallback_model="gemini-test-model",
        include_provider=True,
    )

    assert result[1:] == ("gemini", "gemini-test-model")
    assert len(groq_calls) == 1
    assert len(gemini_calls) == 1
    assert cooldowns.is_blocked("groq")


def test_gemini_request_uses_json_schema_and_returns_text(monkeypatch):
    captured = {}

    class FakeModels:
        def generate_content(self, **kwargs):
            captured["request"] = kwargs
            return SimpleNamespace(
                text='{"mode":"unsupported","tool_calls":[]}'
            )

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client"] = kwargs
            self.models = FakeModels()

    monkeypatch.setattr(planner_provider.genai, "Client", FakeClient)
    result = planner_provider.request_gemini_json(
        system_prompt="synthetic system",
        user_prompt="synthetic user",
        model="gemini-test-model",
        api_key="gemini-test-key",
        timeout=9,
        output_schema=RequestPlan,
    )

    request = captured["request"]
    assert result == '{"mode":"unsupported","tool_calls":[]}'
    assert captured["client"]["api_key"] == "gemini-test-key"
    assert captured["client"]["http_options"].timeout == 9000
    assert request["model"] == "gemini-test-model"
    assert request["contents"] == "synthetic user"
    assert request["config"].system_instruction == "synthetic system"
    assert request["config"].response_mime_type == "application/json"
    schema = request["config"].response_schema
    assert schema.type == planner_provider.genai_types.Type.OBJECT
    assert schema.properties["tool_calls"].items.properties[
        "arguments"
    ].type == planner_provider.genai_types.Type.OBJECT


def test_groq_and_gemini_log_only_token_usage(monkeypatch, caplog):
    class FakeGroq:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **_params: SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                message=SimpleNamespace(
                                    content='{"mode":"unsupported","tool_calls":[]}'
                                )
                            )
                        ],
                        usage=SimpleNamespace(
                            prompt_tokens=11,
                            completion_tokens=7,
                            total_tokens=18,
                        ),
                    )
                )
            )

    class FakeGeminiClient:
        def __init__(self, **_kwargs):
            self.models = SimpleNamespace(
                generate_content=lambda **_params: SimpleNamespace(
                    text='{"mode":"unsupported","tool_calls":[]}',
                    usage_metadata=SimpleNamespace(
                        prompt_token_count=13,
                        candidates_token_count=5,
                        total_token_count=18,
                    ),
                )
            )

    monkeypatch.setattr(planner_provider, "Groq", FakeGroq)
    monkeypatch.setattr(planner_provider.genai, "Client", FakeGeminiClient)
    with caplog.at_level(
        logging.INFO,
        logger="app.agents.planner_provider",
    ):
        planner_provider.request_groq_json(
            system_prompt="synthetic",
            user_prompt="synthetic",
            model="groq-test-model",
            api_key="groq-test-key",
            timeout=1,
        )
        planner_provider.request_gemini_json(
            system_prompt="synthetic",
            user_prompt="synthetic",
            model="gemini-test-model",
            api_key="gemini-test-key",
            timeout=1,
            output_schema=RequestPlan,
        )

    usage_logs = [
        json.loads(record.getMessage().split("planner_token_usage ", 1)[1])
        for record in caplog.records
        if "planner_token_usage " in record.getMessage()
    ]
    assert usage_logs == [
        {
            "provider": "groq",
            "model": "groq-test-model",
            "prompt_tokens": 11,
            "completion_tokens": 7,
            "total_tokens": 18,
        },
        {
            "provider": "gemini",
            "model": "gemini-test-model",
            "prompt_tokens": 13,
            "completion_tokens": 5,
            "total_tokens": 18,
        },
    ]


def test_gemini_service_unavailable_is_mapped_safely(monkeypatch):
    class FakeClient:
        def __init__(self, **_kwargs):
            self.models = SimpleNamespace(
                generate_content=lambda **_params: (_ for _ in ()).throw(
                    planner_provider.genai_errors.ServerError(
                        503,
                        {"error": {"message": "synthetic outage"}},
                    )
                )
            )

    monkeypatch.setattr(planner_provider.genai, "Client", FakeClient)
    with pytest.raises(planner_provider.PlannerProviderError) as error:
        planner_provider.request_gemini_json(
            system_prompt="synthetic",
            user_prompt="synthetic",
            model="gemini-test-model",
            api_key="gemini-test-key",
            timeout=1,
            output_schema=RequestPlan,
        )

    assert error.value.category == "provider_unavailable"
    assert error.value.provider == "gemini"
    assert error.value.model == "gemini-test-model"
    assert "synthetic outage" not in str(error.value)


@pytest.mark.parametrize(
    "category",
    [
        "provider_authentication",
        "provider_misconfigured",
        "provider_bad_request",
    ],
)
def test_groq_configuration_errors_do_not_fall_back_to_gemini(
    monkeypatch,
    category,
):
    def groq_error(**_kwargs):
        raise planner_provider.PlannerProviderError(category)

    monkeypatch.setattr(planner_provider, "request_groq_json", groq_error)
    monkeypatch.setattr(
        planner_provider,
        "request_gemini_json",
        lambda **_kwargs: pytest.fail("Gemini must not mask configuration errors"),
    )

    with pytest.raises(planner_provider.PlannerProviderError) as error:
        planner_provider.request_planner_json(
            provider="groq",
            system_prompt="synthetic",
            user_prompt="synthetic",
            model="groq-model",
            api_key="groq-test-key",
            timeout=1,
            output_schema=RequestPlan,
            ollama_client=object(),
            fallback_api_key="gemini-test-key",
        )

    assert error.value.category == category


def test_orchestrator_passes_gemini_fallback_configuration(monkeypatch):
    captured = {}
    monkeypatch.setattr(settings, "planner_provider", "groq")
    monkeypatch.setattr(settings, "groq_api_key", SecretStr("groq-test-key"))
    monkeypatch.setattr(
        settings,
        "gemini_api_key",
        SecretStr("gemini-test-key"),
    )
    monkeypatch.setattr(settings, "gemini_planner_model", "gemini-test-model")

    def fake_request(**kwargs):
        captured.update(kwargs)
        return '{"mode":"unsupported","tool_calls":[]}', "gemini", "gemini-test-model"

    monkeypatch.setattr(orchestrator, "request_planner_json", fake_request)
    result = orchestrator._request_model_json(
        system_prompt=PLANNER_SYSTEM_PROMPT,
        user_prompt='{"question":"synthetic"}',
        output_schema=RequestPlan,
    )

    assert result == (
        '{"mode":"unsupported","tool_calls":[]}',
        "gemini",
        "gemini-test-model",
    )
    assert captured["fallback_api_key"] == "gemini-test-key"
    assert captured["fallback_model"] == "gemini-test-model"
    assert captured["include_provider"] is True


def test_provider_interface_dispatches_ollama_planner():
    calls = []

    class FakeOllama:
        def chat(self, **kwargs):
            calls.append(kwargs)
            return {"message": {"content": '{"mode":"unsupported","tool_calls":[]}'}}

    result = planner_provider.request_planner_json(
        provider="ollama",
        system_prompt="synthetic system",
        user_prompt="synthetic user",
        model="llama3.2:3b",
        api_key=None,
        timeout=1,
        output_schema=RequestPlan,
        ollama_client=FakeOllama(),
    )

    assert result == '{"mode":"unsupported","tool_calls":[]}'
    assert calls[0]["model"] == "llama3.2:3b"
    assert calls[0]["format"] == RequestPlan.model_json_schema()
    assert calls[0]["options"] == {
        "temperature": 0.0,
        "num_ctx": settings.ollama_num_ctx,
    }
    assert calls[0]["keep_alive"] == settings.ollama_keep_alive


def test_groq_missing_key_is_safe_and_does_not_create_client():
    with pytest.raises(planner_provider.PlannerProviderError) as error:
        planner_provider.request_groq_json(
            system_prompt="synthetic",
            user_prompt="synthetic",
            model="openai/gpt-oss-20b",
            api_key=None,
            timeout=1,
        )

    assert error.value.category == "provider_misconfigured"
    assert error.value.provider == "groq"
    assert "api_key" not in str(error.value)


def test_provider_cooldown_can_be_disabled(monkeypatch):
    cooldowns = planner_provider.ProviderCooldowns(clock=lambda: 10.0)
    monkeypatch.setattr(planner_provider, "provider_cooldowns", cooldowns)
    monkeypatch.setattr(settings, "planner_rate_limit_cooldown_enabled", False)

    planner_provider._record_rate_limit("groq", object())

    assert not planner_provider._provider_is_cooling_down("groq")


def test_token_usage_logging_can_be_disabled(monkeypatch, caplog):
    monkeypatch.setattr(settings, "planner_token_usage_logging_enabled", False)

    with caplog.at_level(
        logging.INFO,
        logger="app.agents.planner_provider",
    ):
        planner_provider._log_token_usage(
            "groq",
            "synthetic-model",
            SimpleNamespace(
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
            ),
            prompt_field="prompt_tokens",
            completion_field="completion_tokens",
            total_field="total_tokens",
        )

    assert not caplog.records


@pytest.mark.parametrize(
    ("exception", "category"),
    [
        (
            planner_provider.AuthenticationError(
                "authentication failure",
                response=httpx.Response(
                    401,
                    request=httpx.Request("POST", "https://api.groq.com"),
                ),
                body=None,
            ),
            "provider_authentication",
        ),
        (
            planner_provider.RateLimitError(
                "rate limit",
                response=httpx.Response(
                    429,
                    request=httpx.Request("POST", "https://api.groq.com"),
                ),
                body=None,
            ),
            "provider_rate_limit",
        ),
        (
            planner_provider.APITimeoutError(
                httpx.Request("POST", "https://api.groq.com")
            ),
            "provider_timeout",
        ),
    ],
)
def test_groq_provider_errors_are_mapped_without_exposing_messages(
    monkeypatch,
    exception,
    category,
):
    monkeypatch.setattr(
        planner_provider,
        "provider_cooldowns",
        planner_provider.ProviderCooldowns(),
    )

    class FakeGroq:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **_params: (_ for _ in ()).throw(exception)
                )
            )

    monkeypatch.setattr(planner_provider, "Groq", FakeGroq)
    with pytest.raises(planner_provider.PlannerProviderError) as error:
        planner_provider.request_groq_json(
            system_prompt="synthetic",
            user_prompt="synthetic",
            model="openai/gpt-oss-20b",
            api_key="test-key",
            timeout=1,
        )

    assert error.value.category == category
    assert "authentication failure" not in str(error.value)
    assert "rate limit" not in str(error.value)
    if category == "provider_rate_limit":
        assert error.value.provider == "groq"


def test_groq_rate_limit_uses_retry_after_and_sets_cooldown(monkeypatch):
    now = [100.0]
    cooldowns = planner_provider.ProviderCooldowns(clock=lambda: now[0])
    monkeypatch.setattr(planner_provider, "provider_cooldowns", cooldowns)
    response = httpx.Response(
        429,
        headers={"Retry-After": "120"},
        request=httpx.Request("POST", "https://api.groq.com"),
    )
    exception = planner_provider.RateLimitError(
        "rate limit",
        response=response,
        body=None,
    )

    class FakeGroq:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **_params: (_ for _ in ()).throw(exception)
                )
            )

    monkeypatch.setattr(planner_provider, "Groq", FakeGroq)
    with pytest.raises(planner_provider.PlannerProviderError):
        planner_provider.request_groq_json(
            system_prompt="synthetic",
            user_prompt="synthetic",
            model="groq-test-model",
            api_key="groq-test-key",
            timeout=1,
        )

    assert cooldowns.is_blocked("groq")
    now[0] += 120
    assert not cooldowns.is_blocked("groq")


def test_rate_limit_cooldown_uses_configured_default_and_clamps_header(
    monkeypatch,
):
    now = [100.0]
    cooldowns = planner_provider.ProviderCooldowns(clock=lambda: now[0])
    monkeypatch.setattr(planner_provider, "provider_cooldowns", cooldowns)
    monkeypatch.setattr(settings, "planner_rate_limit_cooldown_seconds", 600)
    planner_provider._record_rate_limit("gemini", object())

    now[0] += 301
    assert cooldowns.is_blocked("gemini")
    now[0] += 299
    assert not cooldowns.is_blocked("gemini")

    now[0] = 1000.0
    response = httpx.Response(
        429,
        headers={"Retry-After": "999"},
        request=httpx.Request("POST", "https://api.groq.com"),
    )
    planner_provider._record_rate_limit("groq", response)
    now[0] += 300
    assert not cooldowns.is_blocked("groq")


def test_groq_cooldown_skips_primary_and_uses_gemini(monkeypatch):
    cooldowns = planner_provider.ProviderCooldowns(clock=lambda: 10.0)
    cooldowns.block("groq", 30)
    monkeypatch.setattr(planner_provider, "provider_cooldowns", cooldowns)
    monkeypatch.setattr(
        planner_provider,
        "request_groq_json",
        lambda **_kwargs: pytest.fail("cooling Groq provider was called"),
    )
    gemini_calls = []
    monkeypatch.setattr(
        planner_provider,
        "request_gemini_json",
        lambda **kwargs: gemini_calls.append(kwargs)
        or '{"mode":"unsupported","tool_calls":[]}',
    )

    response = planner_provider.request_planner_json(
        provider="groq",
        system_prompt="synthetic",
        user_prompt="synthetic",
        model="groq-test-model",
        api_key="groq-test-key",
        timeout=1,
        output_schema=RequestPlan,
        ollama_client=object(),
        fallback_api_key="gemini-test-key",
        fallback_model="gemini-test-model",
        include_provider=True,
    )

    assert response[1:] == ("gemini", "gemini-test-model")
    assert len(gemini_calls) == 1


def test_groq_cooldown_without_fallback_makes_no_network_call(monkeypatch):
    cooldowns = planner_provider.ProviderCooldowns(clock=lambda: 10.0)
    cooldowns.block("groq", 30)
    monkeypatch.setattr(planner_provider, "provider_cooldowns", cooldowns)
    monkeypatch.setattr(
        planner_provider,
        "request_groq_json",
        lambda **_kwargs: pytest.fail("cooling Groq provider was called"),
    )

    with pytest.raises(planner_provider.PlannerProviderError) as error:
        planner_provider.request_planner_json(
            provider="groq",
            system_prompt="synthetic",
            user_prompt="synthetic",
            model="groq-test-model",
            api_key="groq-test-key",
            timeout=1,
            output_schema=RequestPlan,
            ollama_client=object(),
        )

    assert error.value.category == "provider_rate_limit"
    assert error.value.provider == "groq"


def test_gemini_cooldown_skips_fallback_call(monkeypatch):
    cooldowns = planner_provider.ProviderCooldowns(clock=lambda: 10.0)
    cooldowns.block("groq", 30)
    cooldowns.block("gemini", 30)
    monkeypatch.setattr(planner_provider, "provider_cooldowns", cooldowns)
    monkeypatch.setattr(
        planner_provider,
        "request_gemini_json",
        lambda **_kwargs: pytest.fail("cooling Gemini provider was called"),
    )

    with pytest.raises(planner_provider.PlannerProviderError) as error:
        planner_provider.request_planner_json(
            provider="groq",
            system_prompt="synthetic",
            user_prompt="synthetic",
            model="groq-test-model",
            api_key="groq-test-key",
            timeout=1,
            output_schema=RequestPlan,
            ollama_client=object(),
            fallback_api_key="gemini-test-key",
            fallback_model="gemini-test-model",
        )

    assert error.value.category == "provider_rate_limit"
    assert error.value.provider == "gemini"


def test_rate_limit_failure_is_not_retried_by_model_validation(monkeypatch):
    calls = []

    def rate_limited(**_kwargs):
        calls.append(True)
        raise planner_provider.PlannerProviderError(
            "provider_rate_limit",
            provider="groq",
            model="groq-test-model",
        )

    monkeypatch.setattr(orchestrator, "_request_model_json", rate_limited)
    result = orchestrator.evaluate_planner_query(
        "Synthetic rate-limit test question.",
        provider="groq",
    )

    assert len(calls) == 1
    assert result.error_category == "provider_rate_limit"


def test_planning_outage_returns_failure_answer_and_telemetry(monkeypatch):
    events = []
    monkeypatch.setattr(
        orchestrator,
        "emit_agent_event",
        lambda **event: events.append(event),
    )
    result = orchestrator._final_answer_node(
        {
            "safe_error": "planning_unavailable",
            "plan": RequestPlan(mode="unsupported"),
            "execution_mode": "unsupported",
            "trace_id": "planner-outage-test",
        }
    )

    assert result["answer"] == orchestrator.MODEL_UNAVAILABLE_ANSWER
    assert events[-1]["event"] == "final_response"
    assert events[-1]["outcome"] == "failure"


def test_planner_provider_dispatch_keeps_ollama_answer_path(monkeypatch):
    ollama_calls = []
    planner_calls = []

    class FakeOllama:
        def chat(self, **kwargs):
            ollama_calls.append(kwargs)
            return {"message": {"content": '{"mode":"unsupported","tool_calls":[]}' }}

    monkeypatch.setattr(orchestrator, "_ollama_client", FakeOllama())
    monkeypatch.setattr(settings, "planner_provider", "ollama")
    monkeypatch.setattr(settings, "planner_model", "qwen2.5:7b-instruct")
    monkeypatch.setattr(settings, "llm_model", "llama3.2:3b")
    monkeypatch.setattr(
        orchestrator,
        "request_planner_json",
        lambda **kwargs: planner_calls.append(kwargs)
        or '{"mode":"unsupported","tool_calls":[]}',
    )

    orchestrator._request_model_json(
        system_prompt="synthetic planner prompt",
        user_prompt="synthetic query",
        output_schema=RequestPlan,
    )
    answer = orchestrator._request_model_json(
        system_prompt="answer prompt",
        user_prompt="authorized synthetic evidence",
        output_schema=orchestrator.GeneratedAnswer,
    )

    assert planner_calls[0]["provider"] == "ollama"
    assert planner_calls[0]["model"] == "qwen2.5:7b-instruct"
    assert planner_calls[0]["ollama_client"] is orchestrator._ollama_client
    assert ollama_calls[0]["model"] == "llama3.2:3b"
    assert ollama_calls[0]["format"] == orchestrator.GeneratedAnswer.model_json_schema()
    assert ollama_calls[0]["options"] == {
        "temperature": 0.0,
        "num_ctx": settings.ollama_num_ctx,
    }
    assert ollama_calls[0]["keep_alive"] == settings.ollama_keep_alive
    assert answer == '{"mode":"unsupported","tool_calls":[]}'


def test_groq_planner_dispatch_omits_identity_and_session_context(monkeypatch):
    captured = {}
    monkeypatch.setattr(settings, "planner_provider", "groq")
    monkeypatch.setattr(settings, "planner_model", "openai/gpt-oss-20b")
    monkeypatch.setattr(
        settings,
        "groq_api_key",
        SecretStr("fake-test-key"),
    )

    def fake_groq_request(**kwargs):
        captured.update(kwargs)
        return '{"mode":"unsupported","tool_calls":[]}'

    monkeypatch.setattr(orchestrator, "request_planner_json", fake_groq_request)
    result = orchestrator._request_model_json(
        system_prompt=PLANNER_SYSTEM_PROMPT,
        user_prompt=json.dumps(
            {
                "question": "Synthetic question only",
                "registered_tools": orchestrator._tool_catalog(),
                "max_tool_calls": 3,
            }
        ),
        output_schema=RequestPlan,
    )

    prompt = captured["system_prompt"] + captured["user_prompt"]
    assert result == '{"mode":"unsupported","tool_calls":[]}'
    assert "AUTHENTICATED_ACTOR_SENTINEL" not in prompt
    assert "DATABASE_SESSION_SENTINEL" not in prompt
    assert "JWT_SENTINEL" not in prompt
    assert captured["model"] == "openai/gpt-oss-20b"
    assert captured["api_key"] == "fake-test-key"


def test_groq_invalid_outputs_and_tool_arguments_fail_closed(monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", SecretStr("fake-test-key"))
    outputs = iter(
        [
            "{bad json",
            "{still bad json",
            json.dumps(
                {
                    "mode": "structured",
                    "tool_calls": [
                        {
                            "name": "made_up_tool",
                            "arguments": {},
                        }
                    ],
                }
            ),
            json.dumps(
                {
                    "mode": "structured",
                    "tool_calls": [
                        {
                            "name": "get_project_details",
                            "arguments": {},
                        }
                    ],
                }
            ),
        ]
    )
    monkeypatch.setattr(
        orchestrator,
        "request_planner_json",
        lambda **_kwargs: next(outputs),
    )

    malformed = orchestrator.evaluate_planner_query(
        "Synthetic malformed planner output case.",
        provider="groq",
    )
    unknown_tool = orchestrator.evaluate_planner_query(
        "Synthetic unknown tool case.",
        provider="groq",
    )
    invalid_args = orchestrator.evaluate_planner_query(
        "Synthetic missing required argument case.",
        provider="groq",
    )

    assert malformed.plan is None
    assert malformed.error_category == "invalid_model_json"
    assert unknown_tool.plan is None
    assert unknown_tool.error_category == "invalid_plan"
    assert invalid_args.plan is None
    assert invalid_args.error_category == "invalid_arguments"


@pytest.mark.parametrize(
    "query",
    [
        "Could someone look into that for me?",
        "Show today's live building occupancy.",
    ],
)
def test_ambiguous_and_unavailable_capabilities_can_be_unsupported(
    monkeypatch,
    query,
):
    def unsupported(**_kwargs):
        return '{"mode":"unsupported","tool_calls":[]}'

    monkeypatch.setattr(orchestrator, "request_planner_json", unsupported)
    result = orchestrator.evaluate_planner_query(query, provider="groq")

    assert result.plan == RequestPlan(mode="unsupported")
    assert result.error_category is None


def test_groq_planner_authentication_failure_is_not_retried(monkeypatch):
    attempts = []
    monkeypatch.setattr(settings, "groq_api_key", SecretStr("fake-test-key"))

    def auth_failure(**_kwargs):
        attempts.append(True)
        raise planner_provider.PlannerProviderError(
            "provider_authentication"
        )

    monkeypatch.setattr(orchestrator, "request_planner_json", auth_failure)
    result = orchestrator.evaluate_planner_query(
        "Synthetic authentication failure case.",
        provider="groq",
    )

    assert attempts == [True]
    assert result.error_category == "provider_authentication"


def test_planner_model_falls_back_to_answer_model():
    configured = Settings(
        _env_file=None,
        database_url="sqlite://",
        llm_model="local-answer-model",
        planner_model=None,
    )

    assert configured.effective_planner_model == "local-answer-model"
    assert configured.planner_provider == "ollama"
    assert configured.gemini_planner_model == "gemini-3.8-flash"
    assert configured.planner_fast_path_enabled is True
    assert configured.smalltalk_enabled is True
    assert configured.smalltalk_llm_fallback_enabled is True
    assert configured.smalltalk_llm_timeout_seconds == 20
    assert configured.smalltalk_max_query_characters == 200
    assert configured.smalltalk_max_answer_characters == 600
    assert configured.planner_cache_enabled is True
    assert configured.planner_cache_ttl_seconds == 21600
    assert configured.planner_cache_max_entries == 512
    assert configured.planner_rate_limit_cooldown_seconds == 30
    assert configured.chat_rag_top_k == 4
    assert configured.ollama_host == "http://localhost:11434"
    assert configured.ollama_keep_alive == "30m"
    assert configured.ollama_num_ctx == 4096
    assert configured.rag_max_answer_tokens == 600


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("planner_cache_ttl_seconds", 0),
        ("planner_cache_ttl_seconds", 604801),
        ("planner_cache_max_entries", 0),
        ("planner_cache_max_entries", 10001),
        ("planner_rate_limit_cooldown_seconds", 0),
        ("planner_rate_limit_cooldown_seconds", 601),
        ("chat_rag_top_k", 0),
        ("ollama_num_ctx", 1023),
        ("ollama_num_ctx", 32769),
        ("rag_max_answer_tokens", 63),
        ("rag_max_answer_tokens", 4097),
        ("smalltalk_llm_timeout_seconds", 0),
        ("smalltalk_llm_timeout_seconds", 121),
        ("smalltalk_max_query_characters", 0),
        ("smalltalk_max_answer_characters", 0),
    ],
)
def test_planner_and_ollama_settings_validate_ranges(field, value):
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            database_url="sqlite://",
            **{field: value},
        )


def test_provider_specific_evaluation_and_fixture_are_offline(monkeypatch):
    calls = []
    monkeypatch.setattr(
        runner,
        "evaluate_planner_query",
        lambda query, provider=None: calls.append((query, provider))
        or orchestrator.PlannerEvaluationResult(
            RequestPlan(mode="unsupported"),
            None,
            (),
        ),
    )
    case = load_dataset().cases[0]
    runner.live_predictor(case, provider="groq")
    assert calls == [(case.query, "groq")]

    monkeypatch.setattr(
        runner,
        "live_predictor",
        lambda *_args, **_kwargs: pytest.fail(
            "fixture evaluation must never call a model"
        ),
    )
    report = runner.evaluate_dataset(
        load_dataset(),
        fixture_predictor,
        evaluation_mode="deterministic_fixture",
        provider="groq",
    )
    assert report["planner_provider"] is None
    assert report["planner_model"] is None


def test_groq_provider_model_telemetry_is_counted_and_redacted():
    aggregator = AgentTelemetryAggregator()
    aggregator.record(
        {
            "trace_id": "trace-groq",
            "event": "model_call",
            "outcome": "failure",
            "provider": "groq",
            "model": "openai/gpt-oss-20b",
            "error_category": "provider_timeout",
            "api_key": "SECRET_SENTINEL",
        }
    )
    snapshot = json.dumps(aggregator.snapshot(), sort_keys=True)

    data = json.loads(snapshot)
    assert data["groq"] == {
        "total": 1,
        "success": 0,
        "failure": 1,
        "timeout": 1,
        "success_rate": 0.0,
    }
    assert "openai/gpt-oss-20b" in snapshot
    assert "SECRET_SENTINEL" not in snapshot
