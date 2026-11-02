"""Stateless LangGraph orchestration for authorized company data."""

import json
import logging
import re
from typing import cast

import httpx
import ollama
from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.agents.prompts import ANSWER_SYSTEM_PROMPT, PLANNER_SYSTEM_PROMPT
from app.agents.workflows import (
    MAX_FINAL_ANSWER_CHARACTERS,
    MAX_TOOL_CALLS,
    AgentState,
    AggregatedEvidence,
    GeneratedAnswer,
    RequestPlan,
)
from app.core.config import settings
from app.models.employee import Employee
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


class ModelResponseError(RuntimeError):
    """Model output was unavailable or failed strict validation."""


def _extract_message_content(response: object) -> str:
    if isinstance(response, dict):
        message = response.get("message")
        content = message.get("content") if isinstance(message, dict) else None
    else:
        message = getattr(response, "message", None)
        content = getattr(message, "content", None)
    if not isinstance(content, str) or not content.strip():
        raise ModelResponseError("The model returned no structured content.")
    return content.strip()


def _request_model_json(
    *,
    system_prompt: str,
    user_prompt: str,
    output_schema: type[RequestPlan] | type[GeneratedAnswer],
) -> str:
    response = _ollama_client.chat(
        model=settings.llm_model,
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
) -> RequestPlan | GeneratedAnswer:
    retry_prompt = user_prompt
    for attempt in range(MAX_MODEL_ATTEMPTS):
        try:
            raw = _request_model_json(
                system_prompt=system_prompt,
                user_prompt=retry_prompt,
                output_schema=output_schema,
            )
            return output_schema.model_validate_json(raw)
        except (
            ModelResponseError,
            ValidationError,
            httpx.HTTPError,
            TimeoutError,
            ConnectionError,
            ollama.ResponseError,
        ) as exc:
            if attempt + 1 == MAX_MODEL_ATTEMPTS:
                raise ModelResponseError from exc
            retry_prompt = (
                f"{user_prompt}\n\nYour previous response was invalid. "
                "Return only a valid JSON object matching the required schema."
            )
    raise ModelResponseError


def _tool_catalog() -> list[dict[str, object]]:
    return [
        {
            "name": name,
            "description": definition.description,
            "arguments_schema": definition.input_schema.model_json_schema(),
        }
        for name, definition in TOOLS.items()
    ]


def _validate_plan_tool_arguments(plan: RequestPlan) -> RequestPlan:
    if len(plan.tool_calls) > MAX_TOOL_CALLS:
        raise ValueError("Too many tool calls.")
    for tool_call in plan.tool_calls:
        definition = TOOLS.get(tool_call.name)
        if definition is None:
            raise ValueError("Planner selected an unregistered tool.")
        definition.input_schema.model_validate(tool_call.arguments)
    return plan


def _plan_node(state: AgentState) -> dict[str, object]:
    prompt = json.dumps(
        {
            "question": state["query"],
            "registered_tools": _tool_catalog(),
            "max_tool_calls": MAX_TOOL_CALLS,
        },
        ensure_ascii=False,
    )
    try:
        parsed = _validated_model_output(
            system_prompt=PLANNER_SYSTEM_PROMPT,
            user_prompt=prompt,
            output_schema=RequestPlan,
        )
        plan = _validate_plan_tool_arguments(cast(RequestPlan, parsed))
    except (ModelResponseError, ValidationError, ValueError):
        logger.warning("Structured request planning failed closed.")
        return {
            "plan": RequestPlan(mode="unsupported"),
            "safe_error": "planning_unavailable",
        }
    return {"plan": plan, "safe_error": None}


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
    if has_permission(state["db"], state["actor"], "documents:read"):
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
    return {"safe_error": "permission_denied"}


def _route_authorized_plan(state: AgentState) -> str:
    if state["safe_error"] == "permission_denied":
        return "aggregate"
    return _route_plan(state)


def _retrieval_node(state: AgentState) -> dict[str, object]:
    try:
        result = answer_rag_query(
            state["db"],
            state["actor"],
            state["query"],
            top_k=min(5, settings.rag_max_top_k),
        )
        validated = RAGQueryResponse.model_validate(result)
    except Exception:
        logger.exception("Authorized document retrieval failed.")
        return {
            "rag_result": None,
            "safe_error": "retrieval_unavailable",
        }
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
        try:
            result = execute_tool(
                selected_tool.name,
                selected_tool.arguments,
                db=state["db"],
                actor=state["actor"],
            )
            status_value = result.status
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
            return {
                "tool_results": [],
                "safe_error": "invalid_tool_plan",
            }
        except Exception:
            logger.exception(
                "Registered tool failed during graph execution: %s",
                selected_tool.name,
            )
            state["db"].rollback()
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
    plan = state["plan"]
    if plan is None or plan.mode == "unsupported":
        return {"answer": INSUFFICIENT_CONTEXT_ANSWER}
    if state["safe_error"] == "permission_denied":
        return {"answer": ACCESS_DENIED_ANSWER}
    if state["safe_error"] in {
        "data_unavailable",
        "tool_unavailable",
        "invalid_tool_plan",
    }:
        return {"answer": TOOL_FAILURE_ANSWER}
    if state["safe_error"] == "retrieval_unavailable":
        return {"answer": MODEL_UNAVAILABLE_ANSWER}

    if plan.mode == "rag":
        rag_result = state["rag_result"]
        if rag_result is None:
            return {"answer": MODEL_UNAVAILABLE_ANSWER}
        answer = rag_result.get("answer")
        if not isinstance(answer, str) or len(answer) > MAX_FINAL_ANSWER_CHARACTERS:
            return {"answer": INSUFFICIENT_CONTEXT_ANSWER}
        return {"answer": answer}

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
        )
    except ModelResponseError:
        logger.warning("Grounded answer generation failed closed.")
        return {"answer": MODEL_UNAVAILABLE_ANSWER}
    return {"answer": cast(GeneratedAnswer, generated).answer}


def build_agent_graph():
    graph = StateGraph(AgentState)
    graph.add_node("plan", _plan_node)
    graph.add_node("authorize_plan", _authorize_plan_node)
    graph.add_node("retrieve_documents", _retrieval_node)
    graph.add_node("execute_structured_tools", _execute_tools_node)
    graph.add_node("aggregate_results", _aggregate_node)
    graph.add_node("generate_answer", _final_answer_node)

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

    initial_state: AgentState = {
        "query": query.strip(),
        "actor": actor,
        "db": db,
        "plan": None,
        "safe_error": None,
        "rag_result": None,
        "tool_results": [],
        "evidence": "",
        "answer": "",
        "sources": [],
    }
    result = _agent_graph.invoke(
        initial_state,
        config={"recursion_limit": 8},
    )
    answer = result.get("answer")
    if not isinstance(answer, str) or not answer:
        logger.error("Agent graph completed without a valid answer.")
        answer = INSUFFICIENT_CONTEXT_ANSWER
    if len(answer) > MAX_FINAL_ANSWER_CHARACTERS:
        answer = INSUFFICIENT_CONTEXT_ANSWER
    sources = result.get("sources")
    return {
        "answer": answer,
        "sources": sources if isinstance(sources, list) else [],
    }
