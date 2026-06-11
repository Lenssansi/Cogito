"""MemoryStore —— 进程内会话存储(库自带的默认 SessionStore)。

够用于"装完就能跑一个 agent"。需要重启后还能续会话的,使用方注入持久实现
(如 Cogito backend 的 JSON 文件存储)即可,引擎不变。
"""

from __future__ import annotations


class MemoryStore:
    def __init__(self) -> None:
        self._d: dict[str, dict] = {}

    def load(self, sid: str) -> dict | None:
        v = self._d.get(sid)
        return dict(v) if v is not None else None

    def save(self, sid: str, data: dict) -> None:
        self._d[sid] = dict(data)
