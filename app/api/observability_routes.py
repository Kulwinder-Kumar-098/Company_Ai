"""Protected operational observability endpoints."""

from typing import Any

from fastapi import APIRouter, Depends

from app.core.permissions import require_permission
from app.models.employee import Employee
from app.observability.metrics import get_agent_metrics

router = APIRouter(prefix="/internal", tags=["Internal observability"])


@router.get("/agent-metrics")
def read_agent_metrics(
    _current_user: Employee = Depends(
        require_permission("agent:metrics:read")
    ),
) -> dict[str, Any]:
    """Return bounded process-local aggregates without event payloads."""
    return get_agent_metrics()
