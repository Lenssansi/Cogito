"""DirScope —— 基于目录的路径护栏(库自带的默认 Scope 实现)。

规则:路径 resolve 后必须落在某个授权根目录内(防 ..\\ 目录穿越);
此外,本轮用户明确写出的绝对路径可经 grant_temporary 临时放行——
**不是"非授权目录就一律拒绝",而是用户点名时允许临时访问。**
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
