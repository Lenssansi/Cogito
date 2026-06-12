"""Provider 抽象:统一各厂商差异,循环只面向 cogito_engine.types.Provider 协议。

目前实现 openai_compat(覆盖 DeepSeek/Moonshot/智谱/Ollama 等绝大多数 OpenAI
兼容端点)。后续加 anthropic / gemini 原生格式时,只需在 get_provider 里按
format 分发,上层不用动。
"""

from __future__ import annotations

from typing import Any

import httpx

from .base import ChatMessage, ProviderError, StreamingProvider
from .openai_compat import OpenAICompatProvider, UsageHook


def get_provider(
    cfg: dict[str, Any],
    *,
    on_usage: UsageHook | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> OpenAICompatProvider:
    fmt = (cfg.get("format") or "openai_compat").lower()
    if fmt == "openai_compat":
        return OpenAICompatProvider(
            cfg, on_usage=on_usage, transport=transport
        )
    raise ValueError(f"暂不支持的 API 格式:{fmt}(目前仅 openai_compat)")


__all__ = [
    "ChatMessage",
    "ProviderError",
    "StreamingProvider",
    "OpenAICompatProvider",
    "UsageHook",
    "get_provider",
]
