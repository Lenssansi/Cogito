"""确认策略实现。

- RiskyConfirmPolicy:默认档位策略(从 backend 的 confirm_required 抽来)。
  非高危一律放行;高危按档:none=全静默;all=全确认;risky=仅"不可逆/动
  安全边界"的确认。
- RootConfirmPolicy:"根纪律"策略(Claude Code 式 C 模型)——改动类操作
  发生在根目录**之外** → 永远确认;根内交给 inner 策略。读类不受位置
  约束(读自由,写确认)。
"""

from __future__ import annotations

from pathlib import Path

# 即便在 risky 档也必须确认的:不可逆,或改动安全边界的操作
ALWAYS_CONFIRM = {
    "delete_path",
    "run_command",
    "git_rollback",
    "add_allowed_root",
}


class RiskyConfirmPolicy:
    def __init__(
        self, level: str = "risky", always: set[str] | None = None
    ) -> None:
        self.level = level if level in ("all", "risky", "none") else "risky"
        self.always = set(ALWAYS_CONFIRM if always is None else always)

    def needs_confirm(self, tool_name: str, high_risk: bool,
                      args: dict | None = None) -> bool:
        if not high_risk:
            return False
        if self.level == "none":
            return False
        if self.level == "all":
            return True
        return tool_name in self.always  # risky


# 内置改动类工具 → 决定其"位置"的参数名(run_command 看 cwd,缺省=根内)
MUTATING_PATH_ARGS = {
    "create_file": "path",
    "write_file": "path",
    "edit_file": "path",
    "delete_path": "path",
    "run_command": "cwd",
}


class RootConfirmPolicy:
    def __init__(self, root: str, inner=None,
                 path_args: dict[str, str] | None = None) -> None:
        self.root = Path(root).resolve()
        self.inner = inner or RiskyConfirmPolicy()
        self.path_args = dict(MUTATING_PATH_ARGS if path_args is None
                              else path_args)

    def _outside_root(self, tool_name: str, args: dict | None) -> bool:
        key = self.path_args.get(tool_name)
        if key is None:
            return False
        value = (args or {}).get(key) or ""
        if not value:
            return False  # 缺省 = 根内(工具按 scope.cwd 解析)
        try:
            p = Path(value)
            p = (p if p.is_absolute() else self.root / p).resolve()
            return not (p == self.root or p.is_relative_to(self.root))
        except (OSError, RuntimeError, ValueError):
            return True  # 解析不了的路径按根外处理(保守)

    def needs_confirm(self, tool_name: str, high_risk: bool,
                      args: dict | None = None) -> bool:
        if self._outside_root(tool_name, args):
            return True
        return self.inner.needs_confirm(tool_name, high_risk, args)
