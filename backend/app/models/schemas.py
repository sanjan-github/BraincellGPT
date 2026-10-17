from typing import Literal
from pydantic import BaseModel, Field

Personality = Literal["normal", "sarcastic", "chaotic", "professional", "brutally_honest"]


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=12000)


class ChatRequest(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=12000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=100)
    personality: Personality = "normal"
    memory_enabled: bool = True


class ChatResponse(BaseModel):
    conversation_id: str
    reply: str
    model: str
