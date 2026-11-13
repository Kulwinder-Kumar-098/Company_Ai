
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import logging
from threading import Event, Thread

from fastapi import FastAPI
from fastapi import HTTPException
import httpx
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import engine
from app.core.config import settings
from app.services.document_storage_service import (
    DocumentStorageUnavailableError,
    get_document_storage,
)
from app.services.document_vector_store import (
    VectorStoreUnavailableError,
    get_document_vector_store,
)
from app.workers.document_worker import run_worker
from app.api.routes.knowledge import router as knowledge_router
from app.api.auth_routes import router as auth_router
from app.api.employee_routes import router as employee_router
from app.api.project_routes import router as project_router
from app.api.document_routes import router as document_router
from app.api.chat_routes import router as chat_router
from app.api.rag_routes import router as rag_router
from app.api.tool_routes import router as tool_router
from app.api.observability_routes import router as observability_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    stop_event = Event()
    worker_thread = Thread(
        target=run_worker,
        args=(stop_event,),
        name="document-worker",
        daemon=True,
    )
    worker_thread.start()
    logger.info("Started document processing worker.")
    try:
        yield
    finally:
        stop_event.set()
        worker_thread.join(timeout=5)
        if worker_thread.is_alive():
            logger.warning(
                "Document processing worker did not stop before application shutdown."
            )


app = FastAPI(
    title="Company AI",
    version="0.1.0",
    description="Role-based enterprise AI assistant",
    lifespan=lifespan,
)

app.include_router(knowledge_router)
app.include_router(document_router)
app.include_router(chat_router)
app.include_router(rag_router)
app.include_router(auth_router)
app.include_router(employee_router)
app.include_router(project_router)
app.include_router(tool_router)
app.include_router(observability_router)

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/health/database")
def database_health():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"database": "connected"}
    except SQLAlchemyError:
        # Do not expose database credentials or internal errors.
        raise HTTPException(
            status_code=503,
            detail="Database unavailable",
        )


@app.get("/health/ready")
def readiness():
    dependencies: dict[str, str] = {}

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        dependencies["database"] = "ok"
    except SQLAlchemyError:
        logger.warning("Readiness check failed for the database.")
        dependencies["database"] = "unavailable"

    try:
        get_document_storage().check_connection()
        dependencies["document_storage"] = "ok"
    except (DocumentStorageUnavailableError, OSError, ValueError):
        logger.warning("Readiness check failed for document storage.")
        dependencies["document_storage"] = "unavailable"

    try:
        get_document_vector_store().check_connection()
        dependencies["vector_store"] = "ok"
    except (VectorStoreUnavailableError, OSError, RuntimeError, ValueError):
        logger.warning("Readiness check failed for the vector store.")
        dependencies["vector_store"] = "unavailable"

    try:
        response = httpx.get(
            f"{settings.ollama_host}/api/tags",
            timeout=5.0,
        )
        response.raise_for_status()
        available_models = {
            model.get("name")
            for model in response.json().get("models", [])
            if isinstance(model, dict)
        }
        required_models = {
            settings.embedding_model,
            settings.llm_model,
        }
        dependencies["embedding_service"] = (
            "ok"
            if required_models.issubset(available_models)
            else "unavailable"
        )
    except (httpx.HTTPError, ValueError, AttributeError, TypeError):
        logger.warning("Readiness check failed for the embedding service.")
        dependencies["embedding_service"] = "unavailable"

    if "unavailable" in dependencies.values():
        raise HTTPException(
            status_code=503,
            detail={
                "status": "not_ready",
                "dependencies": dependencies,
            },
        )
    return {"status": "ready", "dependencies": dependencies}
