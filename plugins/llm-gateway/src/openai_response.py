from __future__ import annotations

from typing import Any
from uuid import uuid4


def build_chat_completion_response(content: str, model: str | None) -> dict[str, Any]:
    # OpenAI chat.completion compatible shape（nofx 只依赖 choices[0].message.content）
    return {
        "id": f"chatcmpl_{uuid4().hex}",
        "object": "chat.completion",
        "created": 0,
        "model": model or "gateway",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }

