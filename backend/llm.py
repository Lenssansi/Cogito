"""装配层:backend 构造 provider 的唯一入口。

把引擎(cogito_engine)的 provider 工厂接上本应用的 token 记账(usage):
所有 provider 都经由这里构造,on_usage 只在这一处接线 —— 引擎保持纯净
(不认识 backend),backend 也不必在每个调用点重复传回调。
"""

from __future__ import annotations

from typing import Any

import usage
from cogito_engine.providers import OpenAICompatProvider, get_provider


def build_provider(cfg: dict[str, Any]) -> OpenAICompatProvider:
    return get_provider(cfg, on_usage=usage.record)
