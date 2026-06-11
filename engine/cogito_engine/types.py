"""引擎的核心协议(接口)。

引擎只依赖这些 Protocol;具体实现由使用方注入。这样别人 `pip install
cogito-engine` 后,不需要 Cogito 的 config/store 也能用,只要塞进满足
协议的对象即可。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Provider(Protocol):
    """大模型 provider:给一轮带工具的非流式补全。

    返回 {"content": str, "tool_calls": [{"id","name","arguments":dict}, ...]}。
    """

    async def tool_complete(
        self, messages: list[dict], tools: list[dict]
    ) -> dict: ...


@runtime_checkable
class Scope(Protocol):
    """路径护栏:决定某路径准不准动。

    语义:在授权根目录内 **或** 本轮被临时放行(grant_temporary)→ 允许。
    临时放行 = 用户在消息里明确写出的绝对路径,本轮可访问,即便不在授权根内。
    """

    @property
    def cwd(self) -> str: ...

    def is_allowed(self, path: str) -> bool: ...

    def grant_temporary(self, paths: list[str]) -> None: ...


@runtime_checkable
class ConfirmPolicy(Protocol):
    """某个高危工具执行前要不要弹用户确认。"""

    def needs_confirm(self, tool_name: str, high_risk: bool) -> bool: ...


@runtime_checkable
class SessionStore(Protocol):
    """会话持久化。库自带内存实现;使用方可注入 JSON 文件等持久实现。"""

    def load(self, sid: str) -> dict | None: ...

    def save(self, sid: str, data: dict) -> None: ...
