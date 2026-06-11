"""Provider 抽象。

P1 只实现 openai_compat（覆盖 DeepSeek/Moonshot/智谱/OpenAI 等绝大多数）。
P2/P3 再加 anthropic、gemini，并接入本地 Ollama 路由——届时只需在
get_provider() 里按 format 分发，上层 /api/chat 不用动。
"""

from __future__ import annotations

from typing import Any

from .base import Provider
from .openai_compat import OpenAICompatProvider


def get_provider(cfg: dict[str, Any]) -> Provider:
    fmt = (cfg.get("format") or "openai_compat").lower()
    if fmt == "openai_compat":
        return OpenAICompatProvider(cfg)
    raise ValueError(f"暂不支持的 API 格式：{fmt}（P1 仅 openai_compat）")


__all__ = ["Provider", "get_provider"]
