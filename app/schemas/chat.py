from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class Source(BaseModel):
    name: str
    document_id: int
    page_number: int | None
    chunk_index: int
    score: float


class ChatResponse(BaseModel):
    answer: str
    role: str
    sources: list[Source] = Field(default_factory=list)
