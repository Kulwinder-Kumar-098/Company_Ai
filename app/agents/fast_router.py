"""Conservative deterministic routing for document-policy questions."""

import re

from app.agents.workflows import RequestPlan
from app.core.config import settings

_POLICY_TOPIC = re.compile(
    r"\b(?:polic(?:y|ies)|handbook|guidelines?|leave|holidays?|vacation|"
    r"remote[\s-]+work|work[\s-]+from[\s-]+home|expenses?|reimbursements?|"
    r"travel|benefits|code[\s-]+of[\s-]+conduct|dress[\s-]+code|"
    r"notice[\s-]+period|working[\s-]+hours|attendance|maternity|"
    r"paternity|sick[\s-]+leave)\b",
    re.IGNORECASE,
)
_DATA_INDICATOR = re.compile(
    r"\b(?:my|mine|myself|balance|remaining|taken|pending|approved|rejected|"
    r"status|history|records?|tasks?|projects?|employees?|colleague|"
    r"team|performance|reviews?|salary|payroll|overdue|assigned|assignments?|"
    r"deadline|badge|visitor|department|profile|details|metrics?|ratings?|"
    r"goals?|manager|search|find|allocated|completion|percent(?:age)?|"
    r"quality\s+score|collaboration|occupancy|camera|pay\s+schedule|"
    r"how\s+many|how\s+much|left)\b",
    re.IGNORECASE,
)
_RESOURCE_ID = re.compile(
    r"\b(?:P|T|PRJ|TSK)-[A-Z0-9][A-Z0-9-]*\b|\bEMP-?[0-9][A-Z0-9-]*\b",
    re.IGNORECASE,
)
_GREETING_PREFIX = re.compile(
    r"^\s*(?:h+i+|hlo|helo|hllo|he+y+|hello+|howdy|greetings|"
    r"good\s+(?:morning|afternoon|evening)|namaste|sup|"
    r"what['’]?s\s+up)\b",
    re.IGNORECASE,
)
def fast_route(query: str) -> RequestPlan | None:
    """Route only unambiguous policy/document questions directly to RAG."""
    if not settings.planner_fast_path_enabled or len(query) > 300:
        return None
    if _GREETING_PREFIX.match(query):
        return None
    if not _POLICY_TOPIC.search(query):
        return None
    if _DATA_INDICATOR.search(query) or _RESOURCE_ID.search(query):
        return None
    return RequestPlan(mode="rag")
