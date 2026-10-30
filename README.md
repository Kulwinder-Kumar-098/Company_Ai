# Enterprise AI Agent Platform — Minimal Starter

A small FastAPI starter for a role-based enterprise assistant. Includes demo identity checks, role permissions, a tiny local policy knowledge base, and a modular folder structure.

## Run locally
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```
Open http://127.0.0.1:8000/docs

## Demo users
Use these headers:
- `E001` + `employee`
- `E002` + `hr`
- `E003` + `project_manager`
- `E004` + `cto`

Example:
```bash
curl -X POST http://127.0.0.1:8000/chat -H "Content-Type: application/json" -H "X-Employee-ID: E001" -H "X-Role: employee" -d '{"message":"What is the leave policy?"}'
```

## Security warning
This is a learning scaffold, not production authentication. Do not trust user-controlled role headers in production. Replace the demo dependency with verified SSO/OIDC or JWT identity, load roles from a trusted database, and enforce resource-level authorization before every retrieval and tool call.

## Next steps
1. Add PostgreSQL models and migrations.
2. Replace demo headers with SSO/OIDC.
3. Add PDF ingestion and embeddings (FAISS or pgvector).
4. Add role-filtered retrieval.
5. Add audited HR/project/engineering tools.
6. Add tests for cross-role data leakage.
