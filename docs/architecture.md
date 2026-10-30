# Architecture

```text
Client
  |
  v
FastAPI /chat
  |
  +--> Identity dependency (demo only; replace with SSO/OIDC)
  |
  +--> Chat service / request routing
          |
          +--> Policy retrieval service --> app/knowledge/policies.md
          |
          +--> Future SQL/API tools (backend permission checks required)
          |
          +--> Future LLM orchestration
```

Security principles:
- Authentication establishes identity.
- Backend authorization is checked before retrieval or tool execution.
- The LLM prompt is not a security boundary.
- Filter results by employee, department, project, and sensitivity.
- Audit sensitive reads and writes; require approval for high-impact actions.
