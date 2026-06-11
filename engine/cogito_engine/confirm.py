"""RiskyConfirmPolicy —— 库自带的默认确认档(从 backend 的 confirm_required 抽来)。

- 非高危工具:一律不确认。
- 高危工具按档位:none=全不确认;all=全确认;risky=仅"不可逆 / 动安全边界"的才确认。
"""

from __future__ import annotations

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

    def needs_confirm(self, tool_name: str, high_risk: bool) -> bool:
        if not high_risk:
            return False
        if self.level == "none":
            return False
        if self.level == "all":
            return True
        return tool_name in self.always  # risky
