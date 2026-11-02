"""Authenticated execution endpoint for structured-data tools."""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.core.database import get_db
from app.models.employee import Employee
from app.observability.audit import record_audit_event
from app.schemas.tools import ToolExecuteRequest, ToolExecuteResponse
from app.tools.registry import (
    ToolArgumentsError,
    ToolNotFoundError,
    execute_tool,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tools", tags=["Tools"])

_RESOURCE_ARGUMENTS = ("employee_id", "project_id", "task_id", "department")


def _resource_id(arguments: dict[str, object]) -> str | None:
    for key in _RESOURCE_ARGUMENTS:
        value = arguments.get(key)
        if isinstance(value, str):
            return value
    return None


def _record_execution(
    db: Session,
    actor: Employee,
    *,
    tool_name: str,
    resource_id: str | None,
    outcome: str,
) -> None:
    record_audit_event(
        db,
        actor_employee_id=actor.employee_id,
        action="tool_execute",
        resource_type="structured_tool",
        resource_id=resource_id,
        outcome=outcome,
        details={"tool_name": tool_name},
    )


def _audit_failure(
    db: Session,
    actor: Employee,
    *,
    tool_name: str,
    resource_id: str | None,
) -> None:
    try:
        db.rollback()
        _record_execution(
            db,
            actor,
            tool_name=tool_name,
            resource_id=resource_id,
            outcome="failure",
        )
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Failed to record structured tool execution audit.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Tool execution is temporarily unavailable.",
        ) from None


@router.post(
    "/execute",
    response_model=ToolExecuteResponse,
)
def execute_structured_tool(
    request: ToolExecuteRequest,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ToolExecuteResponse:
    resource_id = _resource_id(request.arguments)

    try:
        result = execute_tool(
            request.tool_name,
            request.arguments,
            db=db,
            actor=current_user,
        )
    except ToolNotFoundError:
        _audit_failure(
            db,
            current_user,
            tool_name=request.tool_name,
            resource_id=resource_id,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Unknown tool.",
        ) from None
    except ToolArgumentsError:
        _audit_failure(
            db,
            current_user,
            tool_name=request.tool_name,
            resource_id=resource_id,
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid tool arguments.",
        ) from None
    except SQLAlchemyError:
        db.rollback()
        logger.exception(
            "Database failure during structured tool execution: %s",
            request.tool_name,
        )
        _audit_failure(
            db,
            current_user,
            tool_name=request.tool_name,
            resource_id=resource_id,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Tool execution is temporarily unavailable.",
        ) from None
    except Exception:
        db.rollback()
        logger.exception(
            "Unexpected failure during structured tool execution: %s",
            request.tool_name,
        )
        _audit_failure(
            db,
            current_user,
            tool_name=request.tool_name,
            resource_id=resource_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Tool execution failed.",
        ) from None

    outcome = result.status
    try:
        _record_execution(
            db,
            current_user,
            tool_name=request.tool_name,
            resource_id=resource_id,
            outcome=outcome,
        )
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Failed to record structured tool execution audit.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Tool execution is temporarily unavailable.",
        ) from None

    if result.status == "denied":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied.",
        )
    if result.status == "not_found":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resource not found.",
        )

    return ToolExecuteResponse(
        tool_name=request.tool_name,
        result=result.model_dump(mode="json"),
    )
