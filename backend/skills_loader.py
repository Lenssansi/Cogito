"""mattpocock/skills 工程类 skill 加载（仅注入「编程」Agent）。

仓库 clone 在 skills/（约 200KB）。只读 engineering 下各 skill 的 SKILL.md。
最看重"盘问/澄清需求再动手"，所以注入时把这条作为硬规则置顶强化，
不只依赖 skill 文本本身。
"""

from __future__ import annotations

from pathlib import Path

from config import PROJECT_ROOT

ENG_DIR = PROJECT_ROOT / "skills" / "skills" / "engineering"

# 置顶硬规则：确保模型真去盘问，而不是把它埋进一堆 skill 文本里被忽略
GRILL_RULE = (
    "【最高优先·需求盘问】动手写/改代码前，先判断需求是否足够明确。"
    "只要存在歧义、缺少关键约束、或你需要做假设，就先停下来，"
    "逐条向用户提问澄清，得到确认后再动手。严禁因需求不清而猜测、"
    "脑补或生成可能错误的代码。宁可多问，不要瞎做。"
)

_MAX_PER = 6000   # 单个 SKILL.md 注入上限
_MAX_TOTAL = 45000  # 注入总上限，防止撑爆上下文


def list_engineering() -> list[str]:
    if not ENG_DIR.is_dir():
        return []
    return sorted(
        d.name for d in ENG_DIR.iterdir()
        if d.is_dir() and (d / "SKILL.md").is_file()
    )


def build_injection(enabled: bool) -> str:
    """返回要拼进编程 Agent 系统提示的文本；未启用或缺失返回 ''。"""
    if not enabled:
        return ""
    names = list_engineering()
    if not names:
        return ""
    parts = [
        GRILL_RULE,
        "\n下面是工程实践指南（写代码时遵循；其中 grill-with-docs 关于"
        "如何盘问澄清需求，务必照做）：",
    ]
    total = 0
    for n in names:
        try:
            txt = (ENG_DIR / n / "SKILL.md").read_text(
                encoding="utf-8", errors="replace"
            ).strip()
        except OSError:
            continue
        if not txt:
            continue
        chunk = f"\n\n===== skill: {n} =====\n{txt[:_MAX_PER]}"
        if total + len(chunk) > _MAX_TOTAL:
            break
        parts.append(chunk)
        total += len(chunk)
    return "".join(parts)


def status(enabled: bool) -> dict:
    names = list_engineering()
    return {
        "enabled": enabled,
        "cloned": ENG_DIR.is_dir(),
        "count": len(names),
        "skills": names,
    }
