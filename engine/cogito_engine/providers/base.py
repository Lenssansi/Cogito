"""Provider 层的公共类型。

协议拆两层(各取所需):
- 循环要的 `Provider`(只有 tool_complete)在 cogito_engine.types;
- 聊天/流式场景要的 `StreamingProvider` 在这里。
OpenAICompatProvider 两个都实现。
"""

from __future__ import annotations

from typing import AsyncIterator, Protocol, TypedDict, runtime_checkable


class ChatMessage(TypedDict):
    role: str  # "user" | "assistant" | "system"
    content: str


class ProviderError(Exception):
    """对用户可读的 provider 错误(缺 key、鉴权失败、上游报错等)。"""


@runtime_checkable
class StreamingProvider(Protocol):
    """可流式对话的 provider(agent 循环不需要;聊天场景用)。

    逐段产出 (kind, text):kind = "answer" 正式回答增量;
    kind = "reasoning" 思考过程增量(如 DeepSeek thinking 的 reasoning_content)。
    """

    def stream_chat(
        self, messages: list[ChatMessage]
    ) -> AsyncIterator[tuple[str, str]]: ...
