"""OpenAI Chat Completions 兼容 schema（nofx 会按该结构发请求/读响应）"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Message(BaseModel):
    role: str
    content: str | None = None


class ChatCompletionRequest(BaseModel):
    model: str | None = None
    messages: list[Message] = Field(default_factory=list)
    temperature: float | None = None
    max_tokens: int | None = None
    max_completion_tokens: int | None = None
    stream: bool | None = None


def extract_system_user(messages: list[Message]) -> tuple[str, str]:
    system = ""
    user = ""
    for m in messages:
        if (m.role or "").strip() == "system" and (m.content or "").strip():
            system = m.content.strip()
        if (m.role or "").strip() == "user" and (m.content or "").strip():
            user = m.content.strip()
    return system, user

