from app.core.security import UserContext, has_permission
from app.services.retrieval_service import search_policy

def answer_message(message: str, user: UserContext) -> tuple[str, list[str]]:
    lower = message.lower()
    if any(word in lower for word in ("policy", "leave", "holiday", "remote", "expense")):
        if not has_permission(user, "policy:read"):
            return "You do not have permission to access company policies.", []
        hits = search_policy(message)
        if not hits:
            return (
                "I couldn't find a relevant answer in the local policy knowledge base. "
                "Add or ingest approved policy documents to improve coverage.",
                [],
            )
        best = hits[0]
        return f"{best['text']}\n\nStarter keyword match — verify against the approved policy source.", [best["source"]]

    if "performance" in lower or "salary" in lower:
        if not has_permission(user, "performance:read"):
            return "You do not have permission to access performance or salary information.", []
        return (
            "The HR data connector is not implemented yet. Add a database query here and "
            "enforce employee/department scope before returning records.",
            [],
        )

    return (
        f"Hello {user.employee_id}. I received your message. Connect an LLM and a "
        "permission-filtered retrieval/tool layer to answer general questions.",
        [],
    )
