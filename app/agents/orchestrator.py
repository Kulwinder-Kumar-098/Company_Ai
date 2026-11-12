"""Stateless LangGraph orchestration for authorized company data."""

import json
import logging
import re
import time
from contextvars import ContextVar
from typing import cast
from uuid import uuid4

import httpx
import ollama
from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.agents.prompts import ANSWER_SYSTEM_PROMPT, PLANNER_SYSTEM_PROMPT
from app.agents.planner_provider import (
    PlannerProviderError,
    request_planner_json,
)
from app.agents.workflows import (
    MAX_FINAL_ANSWER_CHARACTERS,
    MAX_TOOL_CALLS,
    AgentState,
    AggregatedEvidence,
    GeneratedAnswer,
    PlannerEvaluationResult,
    RequestPlan,
)
from app.core.config import settings
from app.models.employee import Employee
from app.observability.agent_telemetry import emit_agent_event
from app.observability.audit import record_audit_event
from app.schemas.rag import RAGQueryResponse
from app.services.authorization_service import has_permission
from app.services.rag_query_service import (
    INSUFFICIENT_CONTEXT_ANSWER,
    answer_rag_query,
)
from app.tools.registry import TOOLS, ToolArgumentsError, execute_tool

logger = logging.getLogger(__name__)

MAX_EVIDENCE_CHARACTERS = 12000
MAX_EVIDENCE_TOOL_ROWS = 15
MAX_MODEL_ATTEMPTS = 2
MODEL_UNAVAILABLE_ANSWER = (
    "I can't answer that right now because the company assistant is "
    "temporarily unavailable."
)
TOOL_FAILURE_ANSWER = (
    "I couldn't complete that request using the available company data."
)
ACCESS_DENIED_ANSWER = (
    "I can't provide an answer because you don't have permission to access "
    "all requested company data."
)

_ollama_client = ollama.Client(
    timeout=settings.rag_generation_timeout_seconds,
)
_planner_provider_override: ContextVar[str | None] = ContextVar(
    "planner_provider_override",
    default=None,
)


class ModelResponseError(RuntimeError):
    """Model output was unavailable or failed strict validation."""

    def __init__(self, error_category: str = "model_unavailable") -> None:
        super().__init__(error_category)
        self.error_category = error_category


class InvalidPlannedArgumentsError(ValueError):
    """A registered tool was selected with invalid or unexpected arguments."""


def _validation_error_category(exc: ValidationError) -> str:
    if any(error.get("type") == "json_invalid" for error in exc.errors()):
        return "invalid_model_json"
    return "invalid_model_schema"


def _extract_message_content(response: object) -> str:
    if isinstance(response, dict):
        message = response.get("message")
        content = message.get("content") if isinstance(message, dict) else None
    else:
        message = getattr(response, "message", None)
        content = getattr(message, "content", None)
    if not isinstance(content, str) or not content.strip():
        raise ModelResponseError("invalid_model_output")
    return content.strip()


def _request_model_json(
    *,
    system_prompt: str,
    user_prompt: str,
    output_schema: type[RequestPlan] | type[GeneratedAnswer],
) -> str | tuple[str, str, str]:
    if output_schema is RequestPlan:
        provider = _planner_provider_override.get() or settings.planner_provider
        api_key = (
            settings.groq_api_key.get_secret_value()
            if settings.groq_api_key is not None
            else None
        )
        fallback_api_key = (
            settings.gemini_api_key.get_secret_value()
            if settings.gemini_api_key is not None
            else None
        )
        return request_planner_json(
            provider=provider,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=settings.effective_planner_model,
            api_key=api_key,
            timeout=settings.rag_generation_timeout_seconds,
            output_schema=output_schema,
            ollama_client=_ollama_client,
            fallback_api_key=fallback_api_key,
            fallback_model=settings.gemini_planner_model,
            include_provider=True,
        )
    else:
        model = settings.llm_model
    response = _ollama_client.chat(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        format=output_schema.model_json_schema(),
        options={"temperature": 0.0},
    )
    return _extract_message_content(response)


def _validated_model_output(
    *,
    system_prompt: str,
    user_prompt: str,
    output_schema: type[RequestPlan] | type[GeneratedAnswer],
    trace_id: str | None = None,
    provider: str | None = None,
) -> RequestPlan | GeneratedAnswer:
    retry_prompt = user_prompt
    last_error_category = "model_unavailable"
    for attempt in range(MAX_MODEL_ATTEMPTS):
        started = time.perf_counter()
        try:
            if provider is not None:
                selected_provider = provider
            elif output_schema is RequestPlan:
                selected_provider = (
                    _planner_provider_override.get()
                    or settings.planner_provider
                )
            else:
                selected_provider = "ollama"
            selected_model = (
                settings.effective_planner_model
                if output_schema is RequestPlan
                else settings.llm_model
            )
            response = _request_model_json(
                system_prompt=system_prompt,
                user_prompt=retry_prompt,
                output_schema=output_schema,
            )
            if isinstance(response, tuple):
                raw, selected_provider, selected_model = response
            else:
                raw = response
            output = output_schema.model_validate_json(raw)
            if trace_id:
                emit_agent_event(
                    trace_id=trace_id,
                    event="model_call",
                    outcome="success",
                    duration_ms=(time.perf_counter() - started) * 1000,
                    error_category=None if attempt == 0 else "retry_succeeded",
                    provider=selected_provider,
                    model=selected_model,
                )
            return output
        except (
            ModelResponseError,
            PlannerProviderError,
            ValidationError,
            httpx.HTTPError,
            TimeoutError,
            ConnectionError,
            ollama.ResponseError,
        ) as exc:
            if isinstance(exc, ModelResponseError):
                category = exc.error_category
            elif isinstance(exc, PlannerProviderError):
                category = exc.category
                selected_provider = exc.provider or selected_provider
                selected_model = exc.model or selected_model
            elif isinstance(exc, ValidationError):
                category = _validation_error_category(exc)
            elif isinstance(exc, (httpx.TimeoutException, TimeoutError)):
                category = "model_timeout"
            else:
                category = "model_unavailable"
            if trace_id:
                emit_agent_event(
                    trace_id=trace_id,
                    event="model_call",
                    outcome="failure",
                    duration_ms=(time.perf_counter() - started) * 1000,
                    error_category=category,
                    provider=selected_provider,
                    model=selected_model,
                )
            if (
                attempt + 1 == MAX_MODEL_ATTEMPTS
                or category in {
                    "provider_authentication",
                    "provider_misconfigured",
                    "provider_bad_request",
                }
            ):
                raise ModelResponseError(category) from exc
            last_error_category = category
            retry_prompt = (
                f"{user_prompt}\n\nYour previous response was invalid. "
                "Return only a valid JSON object matching the required schema."
            )
    raise ModelResponseError(last_error_category)


def _tool_catalog() -> list[dict[str, object]]:
    return [
        {
            "name": name,
            "description": definition.description,
            "arguments_schema": definition.input_schema.model_json_schema(),
        }
        for name, definition in TOOLS.items()
    ]


def validate_registered_plan(plan: RequestPlan) -> RequestPlan:
    if len(plan.tool_calls) > MAX_TOOL_CALLS:
        raise ValueError("Too many tool calls.")
    for tool_call in plan.tool_calls:
        definition = TOOLS.get(tool_call.name)
        if definition is None:
            raise ValueError("Planner selected an unregistered tool.")
        try:
            definition.input_schema.model_validate(tool_call.arguments)
        except ValidationError as exc:
            raise InvalidPlannedArgumentsError from exc
    return plan


def evaluate_planner_query(
    query: str,
    *,
    trace_id: str | None = None,
    provider: str | None = None,
) -> PlannerEvaluationResult:
    """Run and validate one planner result without executing data paths."""
    prompt = json.dumps(
        {
            "question": query,
            "registered_tools": _tool_catalog(),
            "max_tool_calls": MAX_TOOL_CALLS,
        },
        ensure_ascii=False,
    )
    parsed: RequestPlan | GeneratedAnswer | None = None
    provider_token = _planner_provider_override.set(provider)
    try:
        parsed = _validated_model_output(
            system_prompt=PLANNER_SYSTEM_PROMPT,
            user_prompt=prompt,
            output_schema=RequestPlan,
            trace_id=trace_id,
            provider=provider,
        )
        plan = cast(RequestPlan, parsed)
        selected_tool_names = tuple(
            tool.name for tool in plan.tool_calls
        )
        plan = validate_registered_plan(plan)
    except InvalidPlannedArgumentsError:
        return PlannerEvaluationResult(
            plan=None,
            error_category="invalid_arguments",
            selected_tool_names=selected_tool_names,
        )
    except ValidationError:
        return PlannerEvaluationResult(None, "invalid_plan", ())
    except ValueError:
        names = (
            tuple(tool.name for tool in parsed.tool_calls)
            if isinstance(parsed, RequestPlan)
            else ()
        )
        return PlannerEvaluationResult(None, "invalid_plan", names)
    except ModelResponseError as exc:
        return PlannerEvaluationResult(None, exc.error_category, ())
    finally:
        _planner_provider_override.reset(provider_token)
    return PlannerEvaluationResult(plan, None, selected_tool_names)

def _plan_node(state: AgentState) -> dict[str, object]:
    planner_result = evaluate_planner_query(
        state["query"],
        trace_id=state["trace_id"],
    )
    plan = planner_result.plan
    error_category = planner_result.error_category
    if plan is None:
        logger.warning("Agent planner failed closed: %s", error_category)
        return {
            "plan": RequestPlan(mode="unsupported"),
            "execution_mode": "unsupported",
            "safe_error": "planning_unavailable",
        }
    for selected_tool in plan.tool_calls:
        emit_agent_event(
            trace_id=state["trace_id"],
            event="tool_selection",
            outcome="selected",
            mode=plan.mode,
            tool_name=selected_tool.name,
        )
    return {
        "plan": plan,
        "execution_mode": plan.mode,
        "safe_error": None,
    }


def _route_plan(state: AgentState) -> str:
    plan = state["plan"]
    if plan is None or plan.mode == "unsupported":
        return "aggregate"
    if plan.mode == "rag":
        return "retrieve"
    return "execute_tools"


def _authorize_plan_node(state: AgentState) -> dict[str, object]:
    plan = state["plan"]
    if plan is None or plan.mode not in {"rag", "mixed"}:
        return {"safe_error": state["safe_error"]}
    if (
        has_permission(state["db"], state["actor"], "documents:read")
        or has_permission(state["db"], state["actor"], "policy:read")
    ):
        emit_agent_event(
            trace_id=state["trace_id"],
            event="permission_check",
            outcome="allowed",
            mode=plan.mode,
        )
        return {"safe_error": state["safe_error"]}

    record_audit_event(
        state["db"],
        actor_employee_id=state["actor"].employee_id,
        action="read_denied",
        resource_type="permission",
        resource_id="documents:read",
        outcome="denied",
        details={"execution_mode": plan.mode, "source": "rag"},
    )
    emit_agent_event(
        trace_id=state["trace_id"],
        event="permission_check",
        outcome="denied",
        mode=plan.mode,
        error_category="documents_read_denied",
    )
    return {"safe_error": "permission_denied"}


def _route_authorized_plan(state: AgentState) -> str:
    if state["safe_error"] == "permission_denied":
        return "aggregate"
    return _route_plan(state)


def _retrieval_node(state: AgentState) -> dict[str, object]:
    started = time.perf_counter()
    try:
        result = answer_rag_query(
            state["db"],
            state["actor"],
            state["query"],
            top_k=min(5, settings.rag_max_top_k),
            trace_id=state["trace_id"],
        )
        validated = RAGQueryResponse.model_validate(result)
    except Exception:
        logger.warning("Authorized document retrieval failed.")
        emit_agent_event(
            trace_id=state["trace_id"],
            event="rag_execution",
            outcome="failure",
            duration_ms=(time.perf_counter() - started) * 1000,
            mode=state["execution_mode"],
            error_category="retrieval_unavailable",
        )
        return {
            "rag_result": None,
            "safe_error": "retrieval_unavailable",
        }
    emit_agent_event(
        trace_id=state["trace_id"],
        event="rag_execution",
        outcome="success" if validated.sources else "no_results",
        duration_ms=(time.perf_counter() - started) * 1000,
        mode=state["execution_mode"],
    )
    return {
        "rag_result": validated.model_dump(mode="json"),
        "safe_error": state["safe_error"],
    }


def _route_after_retrieval(state: AgentState) -> str:
    return "aggregate"


def _execute_tools_node(state: AgentState) -> dict[str, object]:
    plan = state["plan"]
    if plan is None or len(plan.tool_calls) > MAX_TOOL_CALLS:
        return {
            "tool_results": [],
            "safe_error": "invalid_tool_plan",
        }

    results: list[dict[str, object]] = []
    for selected_tool in plan.tool_calls:
        started = time.perf_counter()
        try:
            result = execute_tool(
                selected_tool.name,
                selected_tool.arguments,
                db=state["db"],
                actor=state["actor"],
            )
            status_value = result.status
            emit_agent_event(
                trace_id=state["trace_id"],
                event="tool_execution",
                outcome=status_value,
                duration_ms=(time.perf_counter() - started) * 1000,
                mode=state["execution_mode"],
                tool_name=selected_tool.name,
                error_category=(
                    "authorization_denied"
                    if status_value == "denied"
                    else None
                ),
            )
            if status_value != "ok":
                return {
                    "tool_results": [],
                    "safe_error": (
                        "permission_denied"
                        if status_value == "denied"
                        else "data_unavailable"
                    ),
                }
            results.append(
                {
                    "tool_name": selected_tool.name,
                    "result": result.model_dump(mode="json"),
                }
            )
        except ToolArgumentsError:
            emit_agent_event(
                trace_id=state["trace_id"],
                event="tool_execution",
                outcome="failure",
                duration_ms=(time.perf_counter() - started) * 1000,
                mode=state["execution_mode"],
                tool_name=selected_tool.name,
                error_category="invalid_arguments",
            )
            return {
                "tool_results": [],
                "safe_error": "invalid_tool_plan",
            }
        except Exception:
            logger.warning(
                "Registered tool failed during graph execution: %s",
                selected_tool.name,
            )
            state["db"].rollback()
            emit_agent_event(
                trace_id=state["trace_id"],
                event="tool_execution",
                outcome="failure",
                duration_ms=(time.perf_counter() - started) * 1000,
                mode=state["execution_mode"],
                tool_name=selected_tool.name,
                error_category="tool_unavailable",
            )
            return {
                "tool_results": [],
                "safe_error": "tool_unavailable",
            }
    return {"tool_results": results}


def _route_after_tools(state: AgentState) -> str:
    if state["safe_error"] is not None:
        return "aggregate"
    if state["plan"] is not None and state["plan"].mode == "mixed":
        return "retrieve"
    return "aggregate"


def _safe_tool_result(
    item: dict[str, object],
    *,
    query: str,
) -> dict[str, object]:
    raw_result = item.get("result")
    if not isinstance(raw_result, dict):
        return {"tool_name": item.get("tool_name"), "status": "unavailable"}
    status_value = raw_result.get("status")
    safe_result: dict[str, object] = {
        "tool_name": item.get("tool_name"),
        "status": status_value if status_value in {
            "ok",
            "denied",
            "not_found",
        } else "unavailable",
    }
    data = raw_result.get("data")
    if status_value == "ok" and data is not None:
        if not re.search(r"\b(email|e-mail|contact)\b", query, re.IGNORECASE):
            data = _remove_email_fields(data)
        if isinstance(data, list):
            data = data[:MAX_EVIDENCE_TOOL_ROWS]
        safe_result["data"] = data
    return safe_result


def _remove_email_fields(data: object) -> object:
    if isinstance(data, dict):
        return {
            key: _remove_email_fields(value)
            for key, value in data.items()
            if key.lower() != "email"
        }
    if isinstance(data, list):
        return [_remove_email_fields(value) for value in data]
    return data


def _aggregate_node(state: AgentState) -> dict[str, object]:
    if state["safe_error"] in {
        "permission_denied",
        "data_unavailable",
        "tool_unavailable",
        "invalid_tool_plan",
        "retrieval_unavailable",
    }:
        return {"evidence": "{}", "sources": []}

    rag_result = state["rag_result"]
    documents: list[dict[str, object]] = []
    if rag_result is not None:
        raw_sources = rag_result.get("sources", [])
        sources = raw_sources if isinstance(raw_sources, list) else []
        documents.append(
            {
                "answer": str(rag_result.get("answer", ""))[:3000],
                "sources": [
                    source
                    for source in sources[:10]
                    if isinstance(source, dict)
                ],
            }
        )
    elif (
        state["plan"] is not None
        and state["plan"].mode in {"rag", "mixed"}
    ):
        documents.append({"status": "unavailable"})

    structured = [
        _safe_tool_result(item, query=state["query"])
        for item in state["tool_results"]
    ]
    evidence = AggregatedEvidence(
        documents=documents,
        structured_results=structured,
    )
    encoded = evidence.model_dump_json()
    if len(encoded) > MAX_EVIDENCE_CHARACTERS:
        for result in structured:
            data = result.get("data")
            if isinstance(data, list):
                result["data"] = data[:3]
        evidence = AggregatedEvidence(
            documents=documents,
            structured_results=structured,
        )
        encoded = evidence.model_dump_json()
    if len(encoded) > MAX_EVIDENCE_CHARACTERS:
        evidence = AggregatedEvidence(
            documents=[
                {
                    "answer": INSUFFICIENT_CONTEXT_ANSWER,
                    "sources": [],
                }
            ]
            if documents
            else [],
            structured_results=[
                {
                    "tool_name": result.get("tool_name"),
                    "status": "result_truncated",
                }
                for result in structured
            ],
        )
        encoded = evidence.model_dump_json()

    sources_out: list[dict[str, object]] = []
    if rag_result is not None:
        raw_sources = rag_result.get("sources", [])
        if isinstance(raw_sources, list):
            for source in raw_sources:
                if not isinstance(source, dict):
                    continue
                sources_out.append(
                    {
                        "source_id": source.get("source_id"),
                        "name": str(source.get("filename", ""))[:255],
                        "document_id": source.get("document_id", 0),
                        "page_number": source.get("page_number"),
                        "chunk_index": source.get(
                            "chunk_index",
                            source.get("chunk_id", 0),
                        ),
                        "score": source.get("relevance_score", 0.0),
                    }
                )
    return {
        "evidence": encoded,
        "sources": sources_out,
    }


def _final_answer_node(state: AgentState) -> dict[str, object]:
    started = time.perf_counter()
    answer: str
    plan = state["plan"]
    if plan is None or plan.mode == "unsupported":
        answer = INSUFFICIENT_CONTEXT_ANSWER
    elif state["safe_error"] == "permission_denied":
        answer = ACCESS_DENIED_ANSWER
    elif state["safe_error"] in {
        "data_unavailable",
        "tool_unavailable",
        "invalid_tool_plan",
    }:
        answer = TOOL_FAILURE_ANSWER
    elif state["safe_error"] == "retrieval_unavailable":
        answer = MODEL_UNAVAILABLE_ANSWER
    elif plan.mode == "rag":
        rag_result = state["rag_result"]
        if rag_result is None:
            answer = MODEL_UNAVAILABLE_ANSWER
        else:
            rag_answer = rag_result.get("answer")
            answer = (
                rag_answer
                if isinstance(rag_answer, str)
                and len(rag_answer) <= MAX_FINAL_ANSWER_CHARACTERS
                else INSUFFICIENT_CONTEXT_ANSWER
            )
    else:
        try:
            generated = _validated_model_output(
                system_prompt=ANSWER_SYSTEM_PROMPT,
                user_prompt=json.dumps(
                    {
                        "question": state["query"],
                        "authorized_evidence": state["evidence"],
                    },
                    ensure_ascii=False,
                ),
                output_schema=GeneratedAnswer,
                trace_id=state["trace_id"],
            )
            answer = cast(GeneratedAnswer, generated).answer
        except ModelResponseError:
            logger.warning("Grounded answer generation failed closed.")
            answer = MODEL_UNAVAILABLE_ANSWER
    if answer == ACCESS_DENIED_ANSWER:
        final_outcome = "denied"
    elif answer == INSUFFICIENT_CONTEXT_ANSWER:
        final_outcome = "abstention"
    elif answer in {TOOL_FAILURE_ANSWER, MODEL_UNAVAILABLE_ANSWER}:
        final_outcome = "failure"
    else:
        final_outcome = "success"
    emit_agent_event(
        trace_id=state["trace_id"],
        event="final_response",
        outcome=final_outcome,
        duration_ms=(time.perf_counter() - started) * 1000,
        mode=state["execution_mode"],
        error_category=state["safe_error"],
    )
    return {"answer": answer}


def _instrument_node(name: str, node):
    def run(state: AgentState) -> dict[str, object]:
        started = time.perf_counter()
        try:
            update = node(state)
        except Exception:
            emit_agent_event(
                trace_id=state["trace_id"],
                event="node_execution",
                outcome="failure",
                duration_ms=(time.perf_counter() - started) * 1000,
                mode=state["execution_mode"],
                node=name,
                error_category="node_failure",
            )
            raise
        mode = update.get("execution_mode", state["execution_mode"])
        error_category = update.get("safe_error", state["safe_error"])
        emit_agent_event(
            trace_id=state["trace_id"],
            event="node_execution",
            outcome="completed",
            duration_ms=(time.perf_counter() - started) * 1000,
            mode=mode if isinstance(mode, str) else None,
            node=name,
            error_category=(
                error_category if isinstance(error_category, str) else None
            ),
        )
        return update

    return run


def build_agent_graph():
    graph = StateGraph(AgentState)
    graph.add_node("plan", _instrument_node("plan", _plan_node))
    graph.add_node(
        "authorize_plan",
        _instrument_node("authorize_plan", _authorize_plan_node),
    )
    graph.add_node(
        "retrieve_documents",
        _instrument_node("retrieve_documents", _retrieval_node),
    )
    graph.add_node(
        "execute_structured_tools",
        _instrument_node("execute_structured_tools", _execute_tools_node),
    )
    graph.add_node(
        "aggregate_results",
        _instrument_node("aggregate_results", _aggregate_node),
    )
    graph.add_node(
        "generate_answer",
        _instrument_node("generate_answer", _final_answer_node),
    )

    graph.add_edge(START, "plan")
    graph.add_edge("plan", "authorize_plan")
    graph.add_conditional_edges(
        "authorize_plan",
        _route_authorized_plan,
        {
            "retrieve": "retrieve_documents",
            "execute_tools": "execute_structured_tools",
            "aggregate": "aggregate_results",
        },
    )
    graph.add_conditional_edges(
        "retrieve_documents",
        _route_after_retrieval,
        {
            "aggregate": "aggregate_results",
        },
    )
    graph.add_conditional_edges(
        "execute_structured_tools",
        _route_after_tools,
        {
            "retrieve": "retrieve_documents",
            "aggregate": "aggregate_results",
        },
    )
    graph.add_edge("aggregate_results", "generate_answer")
    graph.add_edge("generate_answer", END)
    return graph.compile()


_agent_graph = build_agent_graph()


def run_agent_query(
    db: Session,
    actor: Employee,
    query: str,
) -> dict[str, object]:
    if not query.strip():
        raise ValueError("Question cannot be blank.")
    if len(query) > 4000:
        raise ValueError("Question exceeds the supported length.")

    trace_id = str(uuid4())
    graph_started = time.perf_counter()
    initial_state: AgentState = {
        "trace_id": trace_id,
        "query": query.strip(),
        "actor": actor,
        "db": db,
        "plan": None,
        "execution_mode": None,
        "safe_error": None,
        "rag_result": None,
        "tool_results": [],
        "evidence": "",
        "answer": "",
        "sources": [],
        "telemetry_events": [],
    }
    try:
        result = _agent_graph.invoke(
            initial_state,
            config={"recursion_limit": 8},
        )
    except Exception:
        emit_agent_event(
            trace_id=trace_id,
            event="graph_execution",
            outcome="failure",
            duration_ms=(time.perf_counter() - graph_started) * 1000,
            error_category="graph_failure",
        )
        raise
    answer = result.get("answer")
    if not isinstance(answer, str) or not answer:
        logger.error("Agent graph completed without a valid answer.")
        answer = INSUFFICIENT_CONTEXT_ANSWER
    if len(answer) > MAX_FINAL_ANSWER_CHARACTERS:
        answer = INSUFFICIENT_CONTEXT_ANSWER
    safe_error = result.get("safe_error")
    if answer == ACCESS_DENIED_ANSWER:
        graph_outcome = "denied"
    elif answer == INSUFFICIENT_CONTEXT_ANSWER:
        graph_outcome = "abstention"
    elif answer in {TOOL_FAILURE_ANSWER, MODEL_UNAVAILABLE_ANSWER}:
        graph_outcome = "failure"
    elif safe_error is not None:
        graph_outcome = "failure"
    else:
        graph_outcome = "success"
    emit_agent_event(
        trace_id=trace_id,
        event="graph_execution",
        outcome=graph_outcome,
        duration_ms=(time.perf_counter() - graph_started) * 1000,
        mode=(
            result.get("execution_mode")
            if isinstance(result.get("execution_mode"), str)
            else None
        ),
        error_category=(
            result.get("safe_error")
            if isinstance(result.get("safe_error"), str)
            else None
        ),
    )
    sources = result.get("sources")
    return {
        "answer": answer,
        "sources": sources if isinstance(sources, list) else [],
        "trace_id": trace_id,
    }
