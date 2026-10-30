from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import settings


class RAGQueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = Field(default=None, min_length=1, max_length=100)
    top_k: int = Field(default=5, ge=1, le=settings.rag_max_top_k)

    @field_validator("question")
    @classmethod
    def question_must_not_be_blank(cls, value: str) -> str:
        question = value.strip()
        if not question:
            raise ValueError("Question cannot be blank.")
        return question

    @field_validator("conversation_id")
    @classmethod
    def conversation_id_must_not_be_blank(
        cls,
        value: str | None,
    ) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("conversation_id cannot be blank.")
        return value.strip() if value is not None else None

    model_config = ConfigDict(extra="forbid")


class RAGSource(BaseModel):
    source_id: str
    document_id: int
    filename: str
    version_number: int
    page_number: int | None
    chunk_id: int
    relevance_score: float
    excerpt: str


class RAGQueryResponse(BaseModel):
    answer: str
    conversation_id: str
    sources: list[RAGSource] = Field(default_factory=list)
