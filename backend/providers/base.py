from __future__ import annotations

from typing import AsyncIterator, Protocol, TypedDict


class ChatMessage(TypedDict):
    role: str  # "user" | "assistant" | "system"
    content: str


class ProviderError(Exception):
    """对用户可读的 provider 错误（缺 key、鉴权失败、上游报错等）。"""


class Provider(Protocol):
    async def stream_chat(
        self, messages: list[ChatMessage]
    ) -> AsyncIterator[tuple[str, str]]:
        """逐段产出 (kind, text)。

        kind = "answer"  正式回答增量
        kind = "reasoning" 思考过程增量（如 DeepSeek thinking 的 reasoning_content）
        实现需在缺 key/出错时抛 ProviderError。
        """
        ...
