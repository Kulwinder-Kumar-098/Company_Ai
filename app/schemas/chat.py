from pydantic import BaseModel, ConfigDict, Field, field_validator


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=4000)

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, value: str) -> str:
        message = value.strip()
        if not message:
            raise ValueError("Message cannot be blank.")
        return message


class Source(BaseModel):
    source_id: str | None = None
    name: str
    document_id: int
    page_number: int | None
    chunk_index: int
    score: float


class ChatResponse(BaseModel):
    answer: str
    role: str
    sources: list[Source] = Field(default_factory=list)
    trace_id: str | None = None
