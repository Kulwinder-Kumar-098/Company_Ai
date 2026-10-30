# Company AI

A FastAPI enterprise assistant with JWT authentication, database-backed role
permissions, policy retrieval, and resource-scoped employee, project, and task
read APIs.

## Run locally

Configure `DATABASE_URL` and a strong `JWT_SECRET_KEY` in `.env`, then install
dependencies and start the application:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
python -m app.seed
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000/docs to use Swagger. Sign in through
`POST /auth/login` and authorize Swagger with the returned bearer token.

The seed script is additive and idempotent: it inserts missing permissions and
role mappings without deleting existing mappings. It can be safely rerun with
`python -m app.seed`.

## Resource access

- `POST /knowledge/ask` requires `policy:read`.
- HR and admin roles can list and read employee records across departments.
- Employees can list and read only their own employee record.
- Managers and employees can read projects they manage or have a task assigned
  to. Admins can read all projects.
- Project managers can read all tasks in projects they manage; other assigned
  users can read only their own tasks.
- Successful sensitive reads and authorization denials are written to
  `audit_events`.

The existing employee, project, and task APIs are read-only. Their corresponding
write operations, audit coverage for future mutations, and broader
resource-specific business policies should be added before those capabilities
are introduced.

## Tests

```bash
pytest -q tests
```
