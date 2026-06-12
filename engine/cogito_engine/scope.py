"""Scope 实现:路径护栏的两种内置形态。

- DirScope:白名单目录 + 临时授权(编程 Agent 模式)。路径 resolve 后必须
  落在某个授权根内(防 ..\\ 穿越);本轮用户明确写出的绝对路径可经
  grant_temporary 临时放行——**不是"非授权就一律拒绝",而是点名即准**。
- AllowAllScope:全盘放行(文件助手模式)。⚠️ 必须配合"高危工具执行前
  用户确认"(ConfirmPolicy)使用,确认即是这种模式下唯一的安全网。

统一工具集只面向 Scope 协议——两种模式共用同一份工具代码,差异全在这里。
"""

from __future__ import annotations

from pathlib import Path


class DirScope:
    def __init__(self, cwd: str, allowed_roots: list[str]) -> None:
        self._cwd = self._resolve(cwd) or str(Path.cwd())
        self._roots: list[Path] = []
        for r in allowed_roots or []:
            rp = self._resolve(r)
            if rp:
                self._roots.append(Path(rp))
        self._extra: list[Path] = []  # 本轮临时放行(覆盖式)

    @staticmethod
    def _resolve(p: str) -> str:
        try:
            return str(Path(p).resolve())
        except (OSError, RuntimeError):
            return ""

    @property
    def cwd(self) -> str:
        return self._cwd

    def grant_temporary(self, paths: list[str]) -> None:
        """覆盖式设置本轮临时放行的绝对路径(用户在消息里显式写出的)。
        下一轮再调一次换一批,上一轮的自动失效。"""
        out: list[Path] = []
        for p in paths or []:
            try:
                out.append(Path(p).resolve())
            except (OSError, RuntimeError):
                continue
        self._extra = out

    def is_allowed(self, path: str) -> bool:
        if not path:
            return False
        try:
            target = Path(path).resolve()
        except (OSError, RuntimeError):
            return False
        for base in (*self._roots, *self._extra):
            try:
                if target == base or target.is_relative_to(base):
                    return True
            except (OSError, RuntimeError, ValueError):
                continue
        return False


class AllowAllScope:
    """全盘访问(文件助手模式):一切非空路径放行;cwd = 会话基准目录,
    供相对路径解析。grant_temporary 是空操作(本就全放行)。"""

    def __init__(self, cwd: str = "") -> None:
        try:
            self._cwd = str(Path(cwd or Path.cwd()).resolve())
        except (OSError, RuntimeError):
            self._cwd = str(Path.cwd())

    @property
    def cwd(self) -> str:
        return self._cwd

    def grant_temporary(self, paths: list[str]) -> None:
        return None

    def is_allowed(self, path: str) -> bool:
        return bool(path)
