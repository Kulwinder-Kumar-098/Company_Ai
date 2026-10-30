from pathlib import Path
import re

KB_PATH = Path(__file__).resolve().parents[1] / "knowledge" / "policies.md"

def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))

def search_policy(query: str) -> list[dict[str, str]]:
    # Tiny keyword search for starter purposes; replace with embeddings/vector search.
    if not KB_PATH.exists():
        return []
    content = KB_PATH.read_text(encoding="utf-8")
    sections = re.split(r"(?=^## )", content, flags=re.MULTILINE)
    query_tokens = _tokens(query)
    scored = []
    for section in sections:
        lines = section.strip().splitlines()
        if not lines:
            continue
        heading = lines[0].lstrip("# ").strip()
        body = "\n".join(lines[1:]).strip()
        if not body:
            continue
        overlap = len(query_tokens & _tokens(heading + " " + body))
        if overlap:
            scored.append((overlap, {"source": heading, "text": body}))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in scored[:3]]
