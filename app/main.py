
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import engine
from app.api.routes.knowledge import router as knowledge_router
from app.api.auth_routes import router as auth_router
from app.api.employee_routes import router as employee_router
from app.api.project_routes import router as project_router
from app.api.document_routes import router as document_router
from app.api.chat_routes import router as chat_router
from app.api.rag_routes import router as rag_router

app = FastAPI(
    title="Company AI",
    version="0.1.0",
    description="Role-based enterprise AI assistant",
)

app.include_router(knowledge_router)
app.include_router(document_router)
app.include_router(chat_router)
app.include_router(rag_router)
app.include_router(auth_router)
app.include_router(employee_router)
app.include_router(project_router)

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
        from fastapi import HTTPException

        raise HTTPException(
            status_code=503,
            detail="Database unavailable",
        )
