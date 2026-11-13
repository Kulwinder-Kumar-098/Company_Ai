from pathlib import Path

import pytest

from app.agents import orchestrator
from app.agents.fast_router import fast_route
from app.agents.smalltalk import SmalltalkKind, classify_smalltalk
from app.agents.workflows import RequestPlan
from app.core.config import settings
from app.evaluation.runner import load_dataset

EVALUATION_DIR = Path(__file__).parents[1] / "app" / "evaluation"


@pytest.mark.parametrize(
    "query",
    ["hi", "Hello!", "hey there", "Good morning."],
)
def test_fast_greeting_answers_greeting_only_messages(query):
    assert classify_smalltalk(query) is not None


@pytest.mark.parametrize(
    "query",
    [
        "hi, what is the leave policy?",
        "hello, show my project status",
        "hey there, can you help me?",
    ],
)
def test_fast_greeting_defers_questions_to_planner(query):
    assert classify_smalltalk(query) is None


def test_greeting_fast_path_obeys_setting(monkeypatch):
    monkeypatch.setattr(settings, "smalltalk_enabled", False)

    assert classify_smalltalk("hi") is None


@pytest.mark.parametrize(
    "query",
    [
        "How many leave days does John have left?",
        "Summarize the remote-work policy and list my late assignments",
        "Show the office door access badge history",
    ],
)
def test_fast_router_defers_data_or_ambiguous_questions(query):
    assert fast_route(query) is None


@pytest.mark.parametrize(
    "query",
    [
        "Hi, what are the leave policies?",
        "hello show my tasks",
    ],
)
def test_fast_router_defers_greeting_prefixed_questions_to_planner(query):
    assert fast_route(query) is None


def test_policy_question_fast_path_does_not_call_planner(monkeypatch):
    monkeypatch.setattr(settings, "planner_fast_path_enabled", True)
    monkeypatch.setattr(
        orchestrator,
        "request_planner_json",
        lambda **_kwargs: pytest.fail("fast-routed question called planner"),
    )

    update = orchestrator._plan_node(
        {"query": "What does the travel policy say about booking hotels?",
         "trace_id": "fast-path-test"}
    )

    assert update == {
        "plan": RequestPlan(mode="rag"),
        "execution_mode": "rag",
        "safe_error": None,
    }


def test_fast_router_has_perfect_precision_on_both_evaluation_datasets(
    capsys,
):
    datasets = [
        load_dataset(EVALUATION_DIR / "agent_cases.json"),
        load_dataset(EVALUATION_DIR / "agent_cases_holdout.json"),
    ]
    routed_rag_cases = 0
    incorrectly_routed = []

    for dataset in datasets:
        for case in dataset.cases:
            plan = fast_route(case.query)
            if plan is not None and case.expected_mode == "rag":
                routed_rag_cases += 1
            elif plan is not None:
                incorrectly_routed.append(case.case_id)

    print(f"Fast-routed RAG evaluation cases: {routed_rag_cases}")
    assert not incorrectly_routed
    assert "Fast-routed RAG evaluation cases: 4" in capsys.readouterr().out


def test_fast_router_obeys_setting(monkeypatch):
    monkeypatch.setattr(settings, "planner_fast_path_enabled", False)

    assert fast_route("Where is the travel policy?") is None
