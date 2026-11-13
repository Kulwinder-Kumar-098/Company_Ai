# Company AI

A FastAPI enterprise assistant with JWT authentication, database-backed role
permissions, policy and uploaded-document retrieval, and resource-scoped
employee, project, and task read APIs.

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

For Groq planner requests, configure `PLANNER_PROVIDER=groq`,
`GROQ_API_KEY`, and `PLANNER_MODEL`. If Groq is rate-limited, times out, or is
temporarily unavailable, the planner automatically retries the request through
Gemini when `GEMINI_API_KEY` is configured. `GEMINI_PLANNER_MODEL` defaults to
`gemini-3.8-flash`. Gemini is used only as a planner fallback; answer generation
continues to use the configured Ollama model.

Small-talk messages such as greetings, thanks, and capability questions receive
a deterministic local response without planner or RAG calls. For other
unsupported requests, an optional local Ollama small-talk check can be disabled
with `SMALLTALK_LLM_FALLBACK_ENABLED=false`; it receives no company data and
fails closed to the existing insufficient-context answer. Disable all
small-talk handling with `SMALLTALK_ENABLED=false`. The planner also has a
conservative policy-only RAG fast path and an in-process validated-plan cache;
these can be disabled with
`PLANNER_FAST_PATH_ENABLED=false` and `PLANNER_CACHE_ENABLED=false`. The cache
defaults to a six-hour TTL and at most 512 entries. Provider rate limits use a
configurable cooldown, and hosted planner token counts can be disabled with
`PLANNER_TOKEN_USAGE_LOGGING_ENABLED=false`; logs include counts and provider
metadata only, never prompts or answers. `/chat/ask` retrieval defaults to four
chunks, while Ollama model keep-alive, context size, and free-text RAG answer
length are configurable with `CHAT_RAG_TOP_K`, `OLLAMA_KEEP_ALIVE`,
`OLLAMA_NUM_CTX`, and `RAG_MAX_ANSWER_TOKENS`. See `.env.example` for defaults
and limits.

The seed script is additive and idempotent: it inserts missing permissions and
role mappings without deleting existing mappings. It can be safely rerun with
`python -m app.seed`.

Document Q&A uses Ollama for embeddings and answer generation. Start Ollama and
pull the configured models before uploading documents:

```bash
ollama pull nomic-embed-text:latest
ollama pull llama3.2:3b
```

Uploaded PDF, DOCX, and TXT files are saved under `DOCUMENT_STORAGE_DIR`, then
enqueued in the database-backed document-processing queue. Apply migrations
before deploying the API or worker:

```bash
alembic upgrade head
```

The processing-metadata migration clears legacy embeddings and queues existing
documents so their vectors can be regenerated with model metadata. Ensure the
configured Ollama embedding model is available, then run a worker separately
from the API:

```bash
uv run document-worker
# or: python -m app.workers.document_worker
```

Jobs are committed together with document metadata, claimed with PostgreSQL
row-level locks, retried with exponential backoff, and recovered after a worker
crash when its lease expires. Run one or more workers against the same
PostgreSQL database, document storage, and FAISS index path. For multiple hosts,
those paths must be shared storage with atomic file replacement and POSIX file
locking support. Share the FAISS index and its `.metadata.json` sidecar.
Configure polling, lease, retry count, backoff, chunk size,
chunk overlap, and embedding batch/timeout with
`DOCUMENT_WORKER_POLL_SECONDS`, `DOCUMENT_JOB_LEASE_SECONDS`,
`DOCUMENT_JOB_MAX_ATTEMPTS`, `DOCUMENT_JOB_RETRY_BASE_SECONDS`,
`DOCUMENT_CHUNK_SIZE`, `DOCUMENT_CHUNK_OVERLAP`, `EMBEDDING_BATCH_SIZE`, and
`EMBEDDING_TIMEOUT_SECONDS`.

Embeddings are stored with document chunks in the database and indexed at
`DOCUMENT_FAISS_INDEX_PATH`; a JSON sidecar records the embedding model and
dimension. FAISS updates use process/thread locks, and new chunk IDs are added
incrementally. Model mismatches fail indexing rather than mixing incompatible
vectors. The model names can be changed with `EMBEDDING_MODEL` and `LLM_MODEL`;
changing the embedding model requires re-embedding documents before retrieval.
Failed processing is reported through the document's `processing_status`; an
authorized uploader can request reprocessing. Apply the processing-metadata
migration and run the worker to reprocess existing documents before relying on
the new embedding metadata/index manifest.

## Uploaded document access

- `POST /documents/upload` uploads a private, department, or company document.
  Company-wide publication additionally requires `documents:publish`.
- `GET /documents` and `GET /documents/{id}/download` return only documents
  accessible to the signed-in employee; listing shows only each series'
  current version.
- `POST /documents/{id}/versions` creates the next version of an accessible,
  current document and requires `documents:version`. It preserves the original
  access scope and department; uploads must still have unique file bytes.
- `GET /documents/{id}/versions` returns version history only when the signed-in
  employee can access the requested document.
- `GET /documents/{id}/status` returns processing status, active ready version,
  chunk count, completion time, and only a generic safe failure message after
  document-level authorization.
- `POST /documents/{id}/reprocess` retries ingestion for the document's uploader
  with `documents:create`, or a user with `documents:delete`.
- `DELETE /documents/{id}` allows the uploader or a user with
  `documents:delete` to remove the source file, chunks, and vector-index entry.
  A current version with history cannot be deleted individually; delete
  historical versions separately first.
- `POST /chat/ask` accepts `{"message": "..."}` and requires `chat:use`.
  The stateless LangGraph workflow routes each question to document RAG,
  registered structured-data tools, both, or a safe insufficient-information
  response. Document retrieval applies document access rules and excludes
  historical versions before sending chunks to Ollama; answers include source
  filenames and page numbers when available.
- `POST /rag/query` accepts `{"question": "...", "top_k": 5}` and requires
  `documents:read`. It filters to authorized, ready current-version chunks
  before searching FAISS, sends only those retrieved chunks to Ollama, and
  returns source IDs with document/version/page/chunk citations. An optional
  `conversation_id` is echoed for client correlation only: this endpoint is
  stateless and does not persist or reuse conversation history. Configure
  `RAG_MAX_TOP_K`, `RAG_RELEVANCE_THRESHOLD`, `RAG_CONTEXT_MAX_CHARACTERS`,
  `RAG_GENERATION_TIMEOUT_SECONDS`, and `RAG_GENERATION_TEMPERATURE` as needed.
- Uploads with an identical original-file SHA-256 are rejected with HTTP 409,
  even when the filenames differ. Each document is still stored as an
  independent, access-controlled record.
- After extraction, normalized text SHA-256 detects exact content matches and
  64-bit SimHash flags near matches for review without rejecting either. The
  threshold defaults to `0.98` and can be set with
  `DOCUMENT_NEAR_DUPLICATE_THRESHOLD`. `duplicate_review_status` and
  `duplicate_similarity` are returned for accessible documents; no matching
  document identity is disclosed.

The seed grants `documents:read` to employee, manager, HR, and admin roles;
`documents:version` is granted to manager, HR, and admin; and
`documents:delete` is granted only to admin. Rerun `python -m app.seed` after
pulling these permission changes.

## Resource access

- `POST /knowledge/ask` requires `policy:read`.
- HR and admin roles can list and read employee records across departments.
- Employees can list and read only their own employee record.
- Managers and employees can read projects they manage or have a task assigned
  to. Admins can read all projects.
- Project managers can read all tasks in projects they manage; other assigned
  users can read only their own tasks.
- Structured-data tools use the same database-backed employee and project
  permissions. Their backend caller supplies the authenticated employee and
  database session; tool arguments cannot select or impersonate the acting user.
- Performance access has separate, additive permissions: `admin` receives
  `performance:read:all`; `hr` receives
  `performance:read:department`, scoped to the HR user's own department;
  `manager` and `employee` receive `performance:read:self`. The data model has
  no employee-to-manager relationship, so manager performance access is limited
  to the manager's own records. HR department permission does not imply
  cross-department performance access, even though HR can read employee
  profiles across departments. No migration is needed; run
  `python -m app.seed` to add the permissions and role mappings.
- Successful sensitive reads and authorization denials are written to
  `audit_events`.

The existing employee, project, and task APIs are read-only. Their corresponding
write operations, audit coverage for future mutations, and broader
resource-specific business policies should be added before those capabilities
are introduced.

## Structured-data tools

- `POST /tools/execute` accepts `{"tool_name": "...", "arguments": {...}}` and
  returns the tool name with its validated structured result.
- The endpoint requires JWT authentication. It injects the authenticated
  employee and database session into the tool registry; neither identity nor
  session information is accepted from request arguments.
- Only registered tools can run. Tool arguments are validated against each
  registered Pydantic schema, including rejection of extra fields. Existing
  tool-level employee, project, task, and performance authorization remains
  enforced inside each handler.
- Successful and denied executions are audited with the actor, tool name,
  outcome, and a relevant resource ID when present. Tool execution is not
  exposed as an independent LLM function-calling endpoint; chat orchestrates
  tools through the same validated registry.

## LangGraph chat orchestration

`POST /chat/ask` remains the chat endpoint and remains stateless. It requires a
valid JWT and the new `chat:use` permission. Streamlit displays prior turns but
sends only the current message; include needed context again in follow-up
questions. Example:

```json
{"message": "What does our travel policy say, and what are my overdue tasks?"}
```

The graph has six bounded responsibilities:

1. A structured-output planner chooses document RAG, registered structured
   tools, both, or unsupported. It is shown only the registry's tool names,
   descriptions, and argument schemas.
2. A deterministic authorization node checks `documents:read` before any
   planned RAG work. Structured tools continue to enforce their own existing
   granular permissions in their handlers.
3. The RAG node calls the existing `answer_rag_query` service with the
   authenticated employee, preserving document-level access filtering.
4. The tool node executes at most three planned calls through the existing
   validated registry and authorization handlers.
5. The aggregation node validates outputs and bounds evidence supplied to the
   answer model; unrequested email fields are removed from its prompt context.
6. The final node returns the RAG answer directly for document-only requests,
   or generates a concise answer from authorized structured evidence for mixed
   and structured requests.

The additive seed grants `chat:use` to the four existing application roles:
admin, HR, manager, and employee. Those roles already represent the intended
Company AI user categories; unseeded/custom roles do not receive chat access
unless explicitly granted. `chat:use` allows assistant use only; it does not
grant document or structured-data permissions.

Execution-path permissions are independent: RAG requires `documents:read`,
while each structured-data tool keeps its existing permission checks. Mixed
requests use strict all-or-nothing behavior: document permission is checked
before tool execution; if any selected tool is denied, the RAG branch is not
run; if retrieval fails, structured results are suppressed. Any denial returns
a generic access-denied response with no partial result or document citations,
and is audited. Unsupported requests do not access either data source and need
no source-specific permission.

The authenticated employee and SQLAlchemy session come from FastAPI
dependencies and are passed to graph nodes by trusted backend code. Neither is
put in model prompts, accepted from the chat request, or available as model
arguments. No SQL or conversation history is sent to or persisted by the graph.
The planner provider is selected by `PLANNER_PROVIDER` (`ollama` by default or
`groq`); `PLANNER_MODEL` selects its model and falls back to `LLM_MODEL` when
unset. Ollama remains in use for embeddings, RAG, and final answer generation.
Groq planning uses strict JSON Schema output followed by the same Pydantic and
registered-tool validation. Provider failures, malformed model output,
unauthorized data paths, and failed tool execution fail closed with bounded
safe answers.

Chat API behavior is still a single `message` request with a `ChatResponse`,
but answers can now include authorized structured data as well as document
citations. The graph does not persist conversations, stream output, or allow
the model to invoke unregistered tools. The `chat:use` permission is required
for every chat request; data-source permissions are required only when the
corresponding graph path is selected.

## Agent evaluation and observability

The versioned synthetic planner dataset is
[`app/evaluation/agent_cases.json`](./app/evaluation/agent_cases.json). It
contains stable case IDs, synthetic queries, categories, expected execution
mode, expected registry tool names, RAG/citation expectations, expected
authorization outcome, unsupported expectation, and a fixed fixture planner
output. Add cases with unique IDs and no real employee information, secrets,
or confidential document text. Authorization expectations document intended
execution behavior; the planner-only evaluation does not execute data tools or
simulate user permissions.

Run the deterministic evaluator:

```bash
./.venv/bin/python -m app.evaluation.runner --mode fixture
```

Fixture mode checks the dataset and evaluator deterministically. Its scores
measure the evaluator against the supplied synthetic fixed planner outputs;
they are **not** model-quality results. To evaluate the configured planner
against synthetic query text, run:

```bash
./.venv/bin/python -m app.evaluation.runner --mode live --provider ollama
```

For Groq planning, configure `PLANNER_PROVIDER=groq`,
`PLANNER_MODEL=openai/gpt-oss-20b`, and `GROQ_API_KEY`, then run:

```bash
./.venv/bin/python -m app.evaluation.runner --mode live --provider groq
```

Live mode makes planner calls only; it does not execute selected tools,
retrieve actual documents, or evaluate authorization against a production
database. The planner receives only the synthetic question and the registered
tool catalog. Groq receives no employee identity, records, retrieved content,
tool results, answer content, database credentials, or JWTs. Protect
`GROQ_API_KEY` as a secret: keep it in an ignored local `.env` or a deployment
secret manager, never commit it, and do not include it in support logs.
`PLANNER_PROVIDER=ollama` keeps planning local. Use `--dataset PATH` to select
a compatible versioned dataset and `--output PATH` to save the Markdown report.
Reports include the selected provider/model, category and overall routing
accuracy, exact tool-name selection accuracy, invalid-plan and
invalid-argument rates, planner-call
failure rate, unsupported classification accuracy, unnecessary tool-selection
rate, citation-route coverage, and a per-case table of expected and actual
planner decisions. Citation-route coverage measures selection of RAG for cases
marked as citation-requiring, not citation correctness. Execution failure rate
is reported as not measured in planner-only evaluation; runtime RAG/tool
execution outcomes are available as telemetry.

Routing accuracy is exact execution-mode agreement. Tool-selection accuracy is
exact registered tool-name-set agreement. Invalid-plan rate includes malformed
plans and unregistered tool names; invalid-argument rate counts registered
tools whose planner arguments fail their Pydantic schema. Unsupported-query
accuracy is measured only on cases expected to be unsupported. Unnecessary
tool-selection rate counts selected tools on cases whose expected tool set is
empty. Planner-call failure rate counts timeouts, unavailable model responses,
and evaluator failures separately from invalid plans. Case errors distinguish
invalid JSON, valid JSON that violates the plan schema, registered-tool
argument validation failures, unregistered tools, and model timeouts. The
dataset's expected authorization outcomes are policy annotations; they are not
evaluated by the planner runner because it does not execute tools or access
company data.

Graph telemetry is emitted as structured `agent_telemetry` log records and
aggregated in a bounded in-process store. Events include a server-generated
trace ID (also returned in the chat response), execution mode, graph node and
duration, selected registered tool name, planner provider/model and model call
outcome, RAG outcome, tool outcome, final response outcome, and a safe error
category. The internal
`GET /internal/agent-metrics` endpoint exposes aggregate counters, mode
distribution, error categories, latency summaries, and up to 500 recent
trace-level outcomes with registered tool selections and tool outcomes. Access
requires the additive `agent:metrics:read`
permission, granted by the existing seed to admin only; other roles need an
explicit permission grant. The metrics contain no query, prompt, answer,
argument, employee, or document content.

The aggregator is process-local, cumulative for the lifetime of a worker, and
bounded to 5,000 latency samples per series and 500 recent trace summaries.
Counters are not shared across multiple workers and reset on process restart;
the existing structured logs remain available for deployment-level aggregation.
This project currently has no configured OpenTelemetry or Prometheus backend,
and its `docker-compose.yml` is empty. Security-sensitive authorization events
continue to use the existing audit-event infrastructure; high-volume timings
are not stored as audit rows. Telemetry aggregation/logging errors are isolated
from request authorization and execution.

## Tests

```bash
pytest -q tests
```
