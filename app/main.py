
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import engine
from app.api.routes.knowledge import router as knowledge_router
from app.api.auth_routes import router as auth_router


app = FastAPI(
    title="Company AI",
    version="0.1.0",
    description="Role-based enterprise AI assistant",
)

app.include_router(knowledge_router)

app.include_router(auth_router)

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
