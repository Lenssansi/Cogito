"""E2：让普通对话用自然语言改设置（除「全局系统提示词」外）。

读/查类无需确认；改白名单根等高危
由上层会话执行前要用户确认。仅本机（端点层 _local_only 保证）。
"""

from __future__ import annotations

from typing import Any

import config

EXCLUDED = "system_prompt"  # 明确不让对话改


def get_settings_summary() -> dict[str, Any]:
    s = config.load_settings()
    ws = config.get_workspace()
    return {
        "theme": s.get("theme"),
        "confirm_level": config.get_confirm_level(),
        "brain": config.get_brain(),
        "ollama": config.get_ollama(),
        "skills_enabled": config.get_skills_enabled(),
        "active": s.get("active"),
        "providers": [p["name"] for p in s.get("providers", [])],
        "workspace": {"allowed_roots": ws.get("allowed_roots"),
                      "cwd": ws.get("cwd"),
                      "test_cmd": ws.get("test_cmd")},
        "note": "「全局系统提示词」不在对话可改范围内（需到设置页改）",
    }


def set_theme(theme: str) -> dict:
    return {"theme": config.set_theme(theme)}


def set_confirm_level(level: str) -> dict:
    return {"confirm_level": config.set_confirm_level(level)}


def set_brain(auto_route: bool | None = None, local_answer: bool | None = None,
              summary: bool | None = None) -> dict:
    patch = {k: v for k, v in {
        "auto_route": auto_route, "local_answer": local_answer,
        "summary": summary}.items() if v is not None}
    return {"brain": config.set_brain(patch)}


def set_ollama(base_url: str = "", model: str = "") -> dict:
    return {"ollama": config.set_ollama(
        {k: v for k, v in {"base_url": base_url, "model": model}.items() if v})}


def set_skills(enabled: bool) -> dict:
    return {"skills_enabled": config.set_skills_enabled(enabled)}


def set_active(provider_id: str, preset_label: str) -> dict:
    return {"active": config.set_active(provider_id, preset_label)}


def set_cwd(path: str) -> dict:
    return {"workspace": config.set_workspace({"cwd": path})}


def set_test_cmd(cmd: str) -> dict:
    return {"workspace": config.set_workspace({"test_cmd": cmd})}


def add_allowed_root(path: str) -> dict:  # 高危
    ws = config.get_workspace()
    roots = list(ws.get("allowed_roots", []))
    if path not in roots:
        roots.append(path)
    return {"workspace": config.set_workspace({"allowed_roots": roots})}


def remove_allowed_root(path: str) -> dict:
    ws = config.get_workspace()
    roots = [r for r in ws.get("allowed_roots", []) if r != path]
    return {"workspace": config.set_workspace({"allowed_roots": roots})}


def make_description() -> dict:
    """让当前模型为本项目写「说明文档」(项目描述)，写到 说明文档.md。"""
    import asyncio
    from llm import build_provider as _bp
    resolved = config.get_active_resolved()
    if not resolved or not resolved.get("api_key"):
        return {"error": "未配置可用 API"}
    root = config.PROJECT_ROOT
    files = ", ".join(sorted(p.name for p in root.iterdir()))[:600]
    msgs = [
        {"role": "system",
         "content": "为该项目写一份简洁中文「说明文档」(项目描述)："
                    "它是什么、主要功能、定位。仅作项目介绍，不写安装/"
                    "命令/开发细节。只输出 Markdown。"},
        {"role": "user", "content": "项目顶层文件：" + files},
    ]
    try:
        r = asyncio.run(_bp(resolved).tool_complete(msgs, []))
    except Exception as e:  # noqa: BLE001
        return {"error": f"生成失败：{e}"}
    (root / "说明文档.md").write_text(r.get("content", ""),
                                      encoding="utf-8")
    return {"ok": True, "file": "说明文档.md"}


REGISTRY = {
    "get_settings": (get_settings_summary, False),
    "set_theme": (set_theme, False),
    "set_confirm_level": (set_confirm_level, False),
    "set_brain": (set_brain, False),
    "set_ollama": (set_ollama, False),
    "set_skills": (set_skills, False),
    "set_active": (set_active, False),
    "set_cwd": (set_cwd, False),
    "set_test_cmd": (set_test_cmd, False),
    "remove_allowed_root": (remove_allowed_root, False),
    "add_allowed_root": (add_allowed_root, True),
    "make_description": (make_description, False),
}


def is_high_risk(name: str) -> bool:
    return REGISTRY.get(name, (None, True))[1]


def run_tool(name: str, args: dict[str, Any], base: str = "") -> dict:
    if name not in REGISTRY:
        return {"error": f"未知设置工具：{name}"}
    try:
        return REGISTRY[name][0](**(args or {}))
    except TypeError as e:
        return {"error": f"参数错误：{e}"}
    except Exception as e:  # noqa: BLE001
        return {"error": f"执行失败：{e}"}


def tool_specs() -> list[dict[str, Any]]:
    S = "string"
    B = "boolean"

    def fn(n, d, p, r):
        return {"type": "function", "function": {
            "name": n, "description": d,
            "parameters": {"type": "object", "properties": p,
                           "required": r}}}

    return [
        fn("get_settings", "查看当前所有可改设置", {}, []),
        fn("set_theme", "主题 dark|light|system",
           {"theme": {"type": S}}, ["theme"]),
        fn("set_confirm_level",
           "高危确认档 all(每个改动都确认)|risky(默认,仅删/跑命令/"
           "回滚/动安全边界确认)|none(全不确认)",
           {"level": {"type": S}}, ["level"]),
        fn("set_brain", "本地大脑开关",
           {"auto_route": {"type": B}, "local_answer": {"type": B},
            "summary": {"type": B}}, []),
        fn("set_ollama", "Ollama 地址/模型",
           {"base_url": {"type": S}, "model": {"type": S}}, []),
        fn("set_skills", "写码 skills 开关",
           {"enabled": {"type": B}}, ["enabled"]),
        fn("set_active", "切换当前 API+预设",
           {"provider_id": {"type": S}, "preset_label": {"type": S}},
           ["provider_id", "preset_label"]),
        fn("set_cwd", "设当前工作目录(须在白名单内)",
           {"path": {"type": S}}, ["path"]),
        fn("set_test_cmd", "设改后测试命令",
           {"cmd": {"type": S}}, ["cmd"]),
        fn("add_allowed_root", "新增授权工作根目录(高危,会确认)",
           {"path": {"type": S}}, ["path"]),
        fn("remove_allowed_root", "移除授权根目录",
           {"path": {"type": S}}, ["path"]),
        fn("make_description",
           "为本项目生成「说明文档」(项目描述)写入 说明文档.md", {}, []),
    ]
