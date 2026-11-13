from app.agents import orchestrator
from app.agents.plan_cache import PlanCache, plan_cache_key
from app.agents.workflows import PlannerEvaluationResult, RequestPlan
from app.core.config import settings


def _state(query: str, trace_id: str) -> dict[str, str]:
    return {"query": query, "trace_id": trace_id}


def test_plan_cache_key_normalizes_query_without_storing_raw_query():
    first = plan_cache_key("  SHOW   MY profile?! ", "fingerprint", "ollama", "m")
    second = plan_cache_key("show my PROFILE", "fingerprint", "ollama", "m")

    assert first == second
    assert len(first) == 64
    assert "show" not in first


def test_plan_cache_ttl_expires_entries_with_injected_clock():
    now = [10.0]
    cache = PlanCache(clock=lambda: now[0])
    plan = RequestPlan(mode="unsupported")
    cache.put("entry", plan, ttl_seconds=3, max_entries=10)

    assert cache.get("entry") == plan
    now[0] = 13.0
    assert cache.get("entry") is None


def test_plan_cache_evicts_least_recently_used_entry():
    cache = PlanCache()
    first = RequestPlan(mode="unsupported")
    second = RequestPlan(mode="rag")
    cache.put("first", first, ttl_seconds=60, max_entries=2)
    cache.put("second", second, ttl_seconds=60, max_entries=2)

    assert cache.get("first") == first
    cache.put(
        "third",
        RequestPlan(mode="unsupported"),
        ttl_seconds=60,
        max_entries=2,
    )

    assert cache.get("first") == first
    assert cache.get("second") is None


def test_plan_node_reuses_successful_cached_plan(monkeypatch):
    calls = []
    monkeypatch.setattr(settings, "planner_fast_path_enabled", False)
    monkeypatch.setattr(settings, "planner_cache_enabled", True)
    monkeypatch.setattr(orchestrator, "plan_cache", PlanCache())

    def evaluate(query, **_kwargs):
        calls.append(query)
        return PlannerEvaluationResult(RequestPlan(mode="unsupported"), None, ())

    monkeypatch.setattr(orchestrator, "evaluate_planner_query", evaluate)
    first = orchestrator._plan_node(_state("Show my profile.", "trace-one"))
    second = orchestrator._plan_node(_state(" show MY profile ", "trace-two"))

    assert len(calls) == 1
    assert first["plan"] == second["plan"] == RequestPlan(mode="unsupported")


def test_plan_node_does_not_cache_failures(monkeypatch):
    calls = []
    monkeypatch.setattr(settings, "planner_fast_path_enabled", False)
    monkeypatch.setattr(settings, "planner_cache_enabled", True)
    monkeypatch.setattr(orchestrator, "plan_cache", PlanCache())

    def evaluate(query, **_kwargs):
        calls.append(query)
        return PlannerEvaluationResult(None, "provider_unavailable", ())

    monkeypatch.setattr(orchestrator, "evaluate_planner_query", evaluate)
    first = orchestrator._plan_node(_state("Show my profile", "trace-one"))
    second = orchestrator._plan_node(_state("Show my profile", "trace-two"))

    assert len(calls) == 2
    assert first["safe_error"] == second["safe_error"] == "planning_unavailable"


def test_plan_node_cache_fingerprint_change_misses(monkeypatch):
    calls = []
    fingerprints = iter(["fingerprint-one", "fingerprint-two"])
    monkeypatch.setattr(settings, "planner_fast_path_enabled", False)
    monkeypatch.setattr(settings, "planner_cache_enabled", True)
    monkeypatch.setattr(orchestrator, "plan_cache", PlanCache())
    monkeypatch.setattr(
        orchestrator,
        "planner_fingerprint",
        lambda: next(fingerprints),
    )
    monkeypatch.setattr(
        orchestrator,
        "evaluate_planner_query",
        lambda query, **_kwargs: calls.append(query)
        or PlannerEvaluationResult(RequestPlan(mode="unsupported"), None, ()),
    )

    orchestrator._plan_node(_state("Show my profile", "trace-one"))
    orchestrator._plan_node(_state("Show my profile", "trace-two"))

    assert len(calls) == 2


def test_plan_node_cache_setting_bypasses_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(settings, "planner_fast_path_enabled", False)
    monkeypatch.setattr(settings, "planner_cache_enabled", False)
    monkeypatch.setattr(orchestrator, "plan_cache", PlanCache())
    monkeypatch.setattr(
        orchestrator,
        "evaluate_planner_query",
        lambda query, **_kwargs: calls.append(query)
        or PlannerEvaluationResult(RequestPlan(mode="unsupported"), None, ()),
    )

    orchestrator._plan_node(_state("Show my profile", "trace-one"))
    orchestrator._plan_node(_state("Show my profile", "trace-two"))

    assert len(calls) == 2
