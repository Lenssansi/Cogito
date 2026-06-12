"""Cogito 引擎 —— 从零手写的极简 AI agent 内核(循环 + 工具 + provider 抽象)。

设计原则:引擎只认协议(types.py),不认任何具体应用(不 import 任何 backend)。
库自带通用默认实现(DirScope / RiskyConfirmPolicy / MemoryStore),开箱能用;
关键处留接口可注入,使用方(如 Cogito 的 backend)塞自己的实现。
"""

from .types import ConfirmPolicy, Provider, Scope, SessionStore
from .scope import DirScope
from .confirm import RiskyConfirmPolicy
from .store import MemoryStore
from .providers import (
    OpenAICompatProvider,
    ProviderError,
    StreamingProvider,
    get_provider,
)

__version__ = "0.1.0"

__all__ = [
    "Provider",
    "Scope",
    "ConfirmPolicy",
    "SessionStore",
    "DirScope",
    "RiskyConfirmPolicy",
    "MemoryStore",
    "ProviderError",
    "StreamingProvider",
    "OpenAICompatProvider",
    "get_provider",
]
