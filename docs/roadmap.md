# Roadmap

1. Secure identity: SSO/OIDC or verified JWT, server-side role lookup.
2. PostgreSQL models for employees, departments, projects, tasks, and audit events.
3. Ingestion: parse PDF/DOCX/Markdown and track source, version, owner, and access labels.
4. RAG: embeddings + FAISS or pgvector; filter access before context reaches the model.
5. Structured tools: typed tool contracts for HR, project, and engineering metrics.
6. LLM provider adapter (Ollama or hosted API), timeout handling, citations, and refusal behavior.
7. Observability: request IDs, structured logs, audit trail, metrics.
8. Tests: cross-role leakage, forged identity, prompt injection, unauthorized tool calls, stale data, tool failures.
