from pydantic import BaseModel, Field

class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)

class Source(BaseModel):
    name: str

class ChatResponse(BaseModel):
    answer: str
    role: str
    sources: list[Source] = Field(default_factory=list)
