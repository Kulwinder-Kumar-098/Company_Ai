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
- `POST /chat/ask` accepts `{"message": "..."}` and requires `documents:read`.
  Retrieval applies document access rules and excludes historical versions
  before sending chunk text to Ollama; answers include source filenames and page
  numbers when available.
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
