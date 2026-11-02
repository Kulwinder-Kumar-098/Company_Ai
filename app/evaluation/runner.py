"""Evaluate planner routing against the versioned synthetic case dataset."""

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.agents.orchestrator import (
    InvalidPlannedArgumentsError,
    evaluate_planner_query,
    validate_registered_plan,
)
from app.agents.workflows import RequestPlan
from app.agents.workflows import PlannerEvaluationResult
from app.core.config import settings

DEFAULT_DATASET = Path(__file__).with_name("agent_cases.json")


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    category: str = Field(min_length=1)
    query: str = Field(min_length=1, max_length=4000)
    expected_mode: str
    expected_tool_names: list[str]
    expected_rag: bool
    expected_authorization_outcome: str
    expected_citations: bool
    expected_unsupported: bool
    fixture_output: dict[str, object]


class EvaluationDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    description: str
    cases: list[EvaluationCase]


def load_dataset(path: Path = DEFAULT_DATASET) -> EvaluationDataset:
    payload = json.loads(path.read_text(encoding="utf-8"))
    dataset = EvaluationDataset.model_validate(payload)
    case_ids = [case.case_id for case in dataset.cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("Evaluation case IDs must be unique.")
    return dataset


def _validate_prediction(
    payload: dict[str, object],
) -> PlannerEvaluationResult:
    try:
        plan = RequestPlan.model_validate(payload)
    except ValidationError:
        return PlannerEvaluationResult(None, "invalid_plan", ())
    names = tuple(tool.name for tool in plan.tool_calls)
    try:
        return PlannerEvaluationResult(
            validate_registered_plan(plan),
            None,
            names,
        )
    except InvalidPlannedArgumentsError:
        return PlannerEvaluationResult(None, "invalid_arguments", names)
    except ValueError:
        return PlannerEvaluationResult(None, "invalid_plan", names)


def fixture_predictor(
    case: EvaluationCase,
) -> PlannerEvaluationResult:
    """Validate a fixed synthetic planner result; this is not model evaluation."""
    return _validate_prediction(case.fixture_output)


def live_predictor(
    case: EvaluationCase,
    provider: str | None = None,
) -> PlannerEvaluationResult:
    if provider is None:
        return evaluate_planner_query(case.query)
    return evaluate_planner_query(case.query, provider=provider)


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _measure_group(
    cases: list[EvaluationCase],
    predictions: dict[str, PlannerEvaluationResult],
) -> dict[str, object]:
    routing_correct = 0
    tool_correct = 0
    invalid_plans = 0
    invalid_arguments = 0
    unsupported_cases = 0
    unsupported_correct = 0
    unnecessary_tools = 0
    expected_citation_cases = 0
    citation_routes_covered = 0
    planner_call_failures = 0
    case_count = len(cases)

    for case in cases:
        prediction = predictions[case.case_id]
        plan = prediction.plan
        error = prediction.error_category
        if error in {
            "model_timeout",
            "model_unavailable",
            "evaluation_failure",
        }:
            planner_call_failures += 1
        if error in {
            "invalid_plan",
            "invalid_model_json",
            "invalid_model_output",
            "invalid_model_schema",
        }:
            invalid_plans += 1
        elif error == "invalid_arguments":
            invalid_arguments += 1

        actual_mode = plan.mode if plan else None
        actual_tools = sorted(prediction.selected_tool_names)
        if actual_mode == case.expected_mode:
            routing_correct += 1
        if actual_tools == sorted(case.expected_tool_names):
            tool_correct += 1

        if case.expected_unsupported:
            unsupported_cases += 1
            if actual_mode == "unsupported":
                unsupported_correct += 1
        if not case.expected_tool_names and actual_tools:
            unnecessary_tools += 1

        if case.expected_citations:
            expected_citation_cases += 1
            if actual_mode in {"rag", "mixed"}:
                citation_routes_covered += 1

    return {
        "case_count": case_count,
        "routing_accuracy": _ratio(routing_correct, case_count),
        "tool_selection_accuracy": _ratio(tool_correct, case_count),
        "invalid_plan_rate": _ratio(invalid_plans, case_count),
        "invalid_argument_rate": _ratio(invalid_arguments, case_count),
        "unsupported_query_accuracy": _ratio(
            unsupported_correct,
            unsupported_cases,
        ),
        "unnecessary_tool_selection_rate": _ratio(
            unnecessary_tools,
            case_count,
        ),
        "citation_route_coverage": _ratio(
            citation_routes_covered,
            expected_citation_cases,
        ),
        "execution_failure_rate": None,
        "planner_call_failure_rate": _ratio(
            planner_call_failures,
            case_count,
        ),
    }


def evaluate_dataset(
    dataset: EvaluationDataset,
    predictor: Callable[
        [EvaluationCase],
        PlannerEvaluationResult,
    ],
    *,
    evaluation_mode: str,
    provider: str | None = None,
) -> dict[str, object]:
    categories: dict[str, list[EvaluationCase]] = defaultdict(list)
    for case in dataset.cases:
        categories[case.category].append(case)
    predictions: dict[str, PlannerEvaluationResult] = {}
    for case in dataset.cases:
        try:
            predictions[case.case_id] = predictor(case)
        except Exception:
            predictions[case.case_id] = PlannerEvaluationResult(
                None,
                "evaluation_failure",
                (),
            )
    case_results = []
    for case in dataset.cases:
        prediction = predictions[case.case_id]
        actual_mode = prediction.plan.mode if prediction.plan else None
        actual_tools = sorted(prediction.selected_tool_names)
        case_results.append(
            {
                "case_id": case.case_id,
                "category": case.category,
                "expected_mode": case.expected_mode,
                "actual_mode": actual_mode,
                "expected_tool_names": sorted(case.expected_tool_names),
                "selected_tool_names": actual_tools,
                "expected_rag": case.expected_rag,
                "expected_citations": case.expected_citations,
                "expected_authorization_outcome": (
                    case.expected_authorization_outcome
                ),
                "authorization_outcome_measured": False,
                "expected_unsupported": case.expected_unsupported,
                "error_category": prediction.error_category,
                "routing_correct": actual_mode == case.expected_mode,
                "tool_selection_correct": (
                    actual_tools == sorted(case.expected_tool_names)
                ),
            }
        )
    return {
        "dataset_version": dataset.version,
        "evaluation_mode": evaluation_mode,
        "planner_provider": (
            provider or settings.planner_provider
            if evaluation_mode.startswith("live_")
            else None
        ),
        "planner_model": (
            settings.effective_planner_model
            if evaluation_mode.startswith("live_")
            else None
        ),
        "model_quality_evaluated": evaluation_mode.startswith("live_"),
        "metrics": _measure_group(dataset.cases, predictions),
        "categories": {
            category: _measure_group(cases, predictions)
            for category, cases in sorted(categories.items())
        },
        "case_results": case_results,
        "execution_failure_rate_note": (
            "Not measured by planner-only evaluation; runtime tool/RAG "
            "failures are emitted as redacted telemetry."
        ),
    }


def render_report(report: dict[str, object]) -> str:
    metrics = report["metrics"]
    categories = report["categories"]
    assert isinstance(metrics, dict)
    assert isinstance(categories, dict)
    columns = (
        "Category",
        "Cases",
        "Routing",
        "Tool selection",
        "Invalid plan",
        "Invalid args",
        "Unsupported",
        "Unnecessary tools",
        "Citation route",
        "Planner failures",
        "Execution failures",
    )

    def row(label: str, values: dict[str, object]) -> str:
        return "| " + " | ".join(
            (
                label,
                str(values.get("case_count", 0)),
                _format_metric(values.get("routing_accuracy")),
                _format_metric(values.get("tool_selection_accuracy")),
                _format_metric(values.get("invalid_plan_rate")),
                _format_metric(values.get("invalid_argument_rate")),
                _format_metric(values.get("unsupported_query_accuracy")),
                _format_metric(values.get("unnecessary_tool_selection_rate")),
                _format_metric(values.get("citation_route_coverage")),
                _format_metric(values.get("planner_call_failure_rate")),
                _format_metric(values.get("execution_failure_rate")),
            )
        ) + " |"

    lines = [
        "# Agent planner evaluation",
        "",
        f"- Dataset version: {report['dataset_version']}",
        f"- Evaluation mode: {report['evaluation_mode']}",
        f"- Model quality evaluated: {report['model_quality_evaluated']}",
        f"- Planner provider: {report.get('planner_provider') or 'N/A'}",
        f"- Planner model: {report.get('planner_model') or 'N/A'}",
        "",
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
        row("Overall", metrics),
    ]
    for category, values in categories.items():
        if isinstance(values, dict):
            lines.append(row(category, values))
    case_results = [
        item
        for item in report.get("case_results", [])
        if isinstance(item, dict)
    ]
    lines.extend(
        [
            "",
            "## Per-case planner decisions",
            "",
        ]
    )
    if case_results:
        lines.extend(
            [
                "| Case ID | Category | Status | Expected mode | Actual mode | "
                "Expected tools | Selected tools | Error |",
                "| --- | --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for item in case_results:
            matches = (
                item.get("routing_correct")
                and item.get("tool_selection_correct")
                and item.get("error_category") is None
            )
            lines.append(
                "| "
                + " | ".join(
                    (
                        str(item["case_id"]),
                        str(item["category"]),
                        "match" if matches else "review",
                        str(item["expected_mode"]),
                        str(item["actual_mode"]),
                        ", ".join(item["expected_tool_names"]),
                        ", ".join(item["selected_tool_names"]),
                        str(item["error_category"] or "-"),
                    )
                )
                + " |"
            )
    else:
        lines.append("No evaluation cases.")
    lines.extend(
        [
            "",
            str(report["execution_failure_rate_note"]),
            "",
            "Rates are proportions from 0 to 1. Citation route coverage measures "
            "whether cases expecting citations were routed through RAG; it does "
            "not judge citation correctness.",
        ]
    )
    return "\n".join(lines)


def _format_metric(value: object) -> str:
    return "N/A" if value is None else f"{float(value):.2f}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("fixture", "live"),
        default="fixture",
        help=(
            "fixture validates fixed synthetic planner outputs; live calls "
            "the selected planner provider"
        ),
    )
    parser.add_argument(
        "--provider",
        choices=("ollama", "groq"),
        default=settings.planner_provider,
        help="planner provider for live mode; fixture mode never makes calls",
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    dataset = load_dataset(args.dataset)
    if args.mode == "fixture":
        predictor = fixture_predictor
        mode_name = "deterministic_fixture"
        provider = None
    else:
        provider = args.provider
        predictor = lambda case: live_predictor(case, provider=provider)
        mode_name = f"live_{provider}"
    report = render_report(
        evaluate_dataset(
            dataset,
            predictor,
            evaluation_mode=mode_name,
            provider=provider,
        )
    )
    if args.output:
        args.output.write_text(report + "\n", encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
