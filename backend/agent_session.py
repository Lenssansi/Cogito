"""Agent 会话状态机：逐步工具循环 + 高危确认暂停/恢复 + 持久化。

一次只跑一个工具，高危工具执行前暂停要确认；只读自动执行。
任务开始打 git 检查点，可一键回滚。会话(messages+transcript+checkpoint)
持久化到 data/agent_sessions.json，后端重启后仍可载回并继续。
"""

from __future__ import annotations

import json
import uuid
from typing import Any, AsyncIterator

from config import (
    confirm_required,
    get_active_resolved,
    get_skills_enabled,
    get_workspace,
    path_in_scope,
)
import re

from skills_loader import build_injection
from providers.openai_compat import OpenAICompatProvider
from agent_tools import (
    is_high_risk, run_tool, set_extra_paths, set_todos, tool_specs,
)
import userdirs

# Windows 绝对路径 C:\foo\bar 或 D:/baz; 从用户消息抽取
_ABSPATH_RE = re.compile(
    r"[A-Za-z]:[\\/](?:[^\s'\"<>|?*\n]+)"
)


_TRIM = ".,;:!?)】」』\"'“”‘’《》）"


def _extract_paths(text: str) -> list[str]:
    if not text:
        return []
    out = []
    for m in _ABSPATH_RE.finditer(text):
        p = m.group(0).rstrip(_TRIM)
        if p:
            out.append(p)
    return out


def _augment_task_with_extras(task: str, extras: list[str]) -> str:
    """把临时授权直接拼到 user 消息(模型看)的开头,强制其当轮遵守。
    UI 不显示这段(transcript 用原文)。"""
    if not extras:
        return task
    bullets = "\n".join(f"  - {p}" for p in extras)
    return (
        "[本轮临时授权——以下绝对路径已为本次回答放行,**绝不要**"
        "回答『无权限/不在白名单/请加入白名单』,也**绝不要**假定"
        "文件不存在;先用 read_file/list_dir/edit_file 等工具实际"
        "尝试,工具真的报错了再据实回报]:\n"
        f"{bullets}\n\n"
        "用户原文:\n" + task
    )
from store import get_agent, save_agent

RUNS: dict[str, dict[str, Any]] = {}

_PREAMBLE_HEADS = ("我们先", "我先", "我会", "让我先", "让我", "先来",
                   "先看", "先定位", "先分析", "先检查", "先读")


def _looks_like_preamble(content: str | None) -> bool:
    """开头像"我会先/我们先/让我先…"且字数短 → 视为只铺垫没动手。"""
    s = (content or "").strip()
    if not s or len(s) > 220:
        return False
    head = s[:30]
    return any(kw in head for kw in _PREAMBLE_HEADS)


def _has_tools_since_last_user(msgs: list[dict]) -> bool:
    """判断当前 user 轮次之后是否已调过工具——避免对收尾总结也 nudge。"""
    last_user_idx = -1
    for i in range(len(msgs) - 1, -1, -1):
        if msgs[i].get("role") == "user":
            last_user_idx = i
            break
    if last_user_idx < 0:
        return False
    return any(m.get("role") == "tool" for m in msgs[last_user_idx:])


def _persist(rid: str) -> None:
    run = RUNS.get(rid)
    if not run:
        return
    save_agent(rid, {
        "title": run.get("title", "(未命名)"),
        "cwd": run.get("cwd", ""),
        "checkpoint": run.get("checkpoint"),
        "messages": run.get("messages", []),
        "transcript": run.get("transcript", []),
        "status": run.get("status", "done"),
        "web_on": run.get("web_on", True),
        "todos": run.get("todos", []),
    })


def _ensure_loaded(rid: str) -> bool:
    """内存没有就从盘载回（后端重启后仍可继续旧会话）。"""
    if rid in RUNS:
        return True
    s = get_agent(rid)
    if not s:
        return False
    RUNS[rid] = {
        "messages": s.get("messages", []),
        "transcript": s.get("transcript", []),
        "batch": [],
        "bi": 0,
        "checkpoint": s.get("checkpoint"),
        "status": s.get("status", "done"),
        "title": s.get("title", "(未命名)"),
        "cwd": s.get("cwd", ""),
        "web_on": s.get("web_on", True),
        "nudged_this_turn": False,
        "todos": s.get("todos", []),
    }
    return True


def _rec(run: dict, obj: dict) -> bytes:
    """记录事件到 transcript（供持久化/重载渲染）并编码为 SSE。"""
    run.setdefault("transcript", []).append(obj)
    return _sse(obj)

_SYS = (
    "你是 ai-helper 的编程 Agent。只能在当前工作目录及授权白名单内操作。"
    "用提供的工具完成用户的编程/文件任务：先用只读工具(list_dir/read_file/"
    "search_text)了解情况，再做最小且精确的改动(edit_file 优先于 write_file)。"
    "改完代码调用 run_tests 验证。一次只调用一个工具。"
    "凡是涉及第三方库/框架/API 的用法、版本差异、报错信息、或任何你不确定"
    "的最新信息，务必先调用 web_search 查官方文档/资料再动手，不要凭记忆"
    "硬写。{dirs}涉及用户目录时用上述真实路径或 user_dirs 工具，别猜。"
    "【铁律】禁止只输出『我们先来…』『我会先…』『让我先…』这种铺垫性"
    "文字然后就停下——要么立刻调用工具开始干活，要么任务真完成了再用"
    "简洁中文给出最终总结。**只用纯文本回复 = 你的本次响应结束**，"
    "不要把它当成『准备动手』的开场白。"
    "【临时授权】用户在消息中**明确写出的绝对路径**(如 D:\\foo)即便不在"
    "白名单也允许本轮访问;你可以直接对这些路径使用工具,无需先建议加"
    "白名单。"
    "【待办清单】对**非简单任务**(超过 2 步),开局第一个工具调用就是"
    "todo_set,列 3-8 项干练标题,status 全 pending;每开始一项就 "
    "todo_update 改 in_progress,完成立刻 todo_update 改 completed。"
    "前端会用环形进度条实时展示给用户。简单任务(如读一个文件)免列。"
    "当前工作目录：{cwd}"
)


def _provider() -> tuple[OpenAICompatProvider | None, str]:
    """选一个【支持工具调用】的 provider;思考模式自动兜底到本地。"""
    from config import resolve_tool_capable
    r, err = resolve_tool_capable()
    if err:
        return None, err
    if not r or not r.get("api_key"):
        return None, "未配置可用 API key"
    return OpenAICompatProvider(r), ""


def start_run(task: str, web: bool = True) -> tuple[str | None, str | None]:
    """返回 (run_id, error)。校验工作区与 API。"""
    ws = get_workspace()
    cwd = (ws.get("cwd") or "").strip()
    if not cwd:
        return None, "未设置工作目录（设置页配置授权根目录并选当前工作目录）"
    if not path_in_scope(cwd):
        return None, f"工作目录不在授权白名单内：{cwd}"
    import os
    if not os.path.isdir(os.path.join(cwd, ".git")):
        return None, f"工作目录不是 git 仓库：{cwd}（需要 git 安全网，请先 git init）"
    prov, perr = _provider()
    if prov is None:
        return None, perr or "未配置可用的云端 API"
    sys_content = _SYS.format(cwd=cwd, dirs=userdirs.prompt_hint())
    skills = build_injection(get_skills_enabled())
    if skills:
        sys_content = skills + "\n\n" + sys_content
    rid = uuid.uuid4().hex[:12]
    # 本轮临时授权用户消息中明确写出的绝对路径,使 _safe 放行
    extras = _extract_paths(task)
    set_extra_paths(extras)
    # 给模型看的是「附带授权提示的版本」,给 UI 的 transcript 仍是原文
    augmented = _augment_task_with_extras(task, extras)
    RUNS[rid] = {
        "messages": [
            {"role": "system", "content": sys_content},
            {"role": "user", "content": augmented},
        ],
        "transcript": [{"type": "user", "content": task}],
        "batch": [],          # 当前待处理 tool_calls
        "bi": 0,
        "checkpoint": None,
        "status": "running",  # running | awaiting | done | error
        "title": (task.strip()[:40] or "(未命名)"),
        "cwd": cwd,
        "nudged_this_turn": False,  # 反铺垫骤停:每轮最多 nudge 一次
        "web_on": bool(web),       # 是否允许 Agent 调 web_search 工具
        "extra_paths": extras,     # 本轮临时授权路径(消息中明确写出的)
        "todos": [],               # Claude 风格的待办清单 + 进度
    }
    _persist(rid)
    return rid, None


def _sse(obj: dict) -> bytes:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode("utf-8")


async def _drive(rid: str) -> AsyncIterator[bytes]:
    run = RUNS[rid]
    # 把本 run 的临时授权路径推到 agent_tools 模块全局,_safe 据此放行
    set_extra_paths(run.get("extra_paths") or [])
    # 把本 run 的 todos 推回模块全局,让 todo_update 在正确的列表上操作
    set_todos(run.get("todos") or [])
    prov, perr = _provider()
    if prov is None:
        run["status"] = "error"
        yield _rec(run, {"type": "error", "error": perr or "云端 API 不可用"})
        _persist(rid)
        return

    # 任务开始打 git 检查点（仅一次）
    if run["checkpoint"] is None:
        cp = run_tool("git_checkpoint", {"message": "ai-helper agent 起点"})
        if "error" in cp:
            run["status"] = "error"
            yield _rec(run, {"type": "error",
                             "error": f"无法建检查点：{cp['error']}"})
            _persist(rid)
            return
        run["checkpoint"] = cp["checkpoint"]
        yield _rec(run, {"type": "checkpoint", "commit": cp["checkpoint"]})

    while True:
        # 1) 处理当前 batch 里未完成的 tool_call
        while run["bi"] < len(run["batch"]):
            c = run["batch"][run["bi"]]
            if (confirm_required(c["name"], is_high_risk(c["name"]))
                    and not c.get("_decided")):
                run["status"] = "awaiting"
                yield _rec(run, {"type": "confirm", "tool": c["name"],
                                 "args": c["arguments"],
                                 "call_id": c["id"]})
                _persist(rid)
                return  # 等 /respond
            if c.get("_denied"):
                content = "用户拒绝执行该操作。请换一种安全的方式或询问用户。"
            else:
                yield _rec(run, {"type": "tool", "name": c["name"],
                                 "args": c["arguments"]})
                res = run_tool(c["name"], c["arguments"])
                content = json.dumps(res, ensure_ascii=False)[:8000]
                yield _rec(run, {"type": "result", "name": c["name"],
                                 "result": res})
                # todo_set/todo_update 修改了清单 → 推送 todos 事件给前端
                if c["name"] in ("todo_set", "todo_update") and isinstance(
                    res, dict
                ) and "todos" in res:
                    run["todos"] = res["todos"]
                    yield _rec(run, {"type": "todos",
                                      "items": res["todos"]})
            run["messages"].append({
                "role": "tool", "tool_call_id": c["id"],
                "content": content,
            })
            run["bi"] += 1

        # 2) batch 处理完，问模型下一步
        all_tools = tool_specs()
        if not run.get("web_on", True):
            # 关闭联网 → 不暴露 web_search 工具给模型
            all_tools = [t for t in all_tools
                         if t["function"]["name"] != "web_search"]
        try:
            resp = await prov.tool_complete(run["messages"], all_tools)
        except Exception as e:  # noqa: BLE001
            run["status"] = "error"
            yield _rec(run, {"type": "error",
                             "error": f"模型调用失败：{e}"})
            _persist(rid)
            return

        asst: dict[str, Any] = {"role": "assistant",
                                "content": resp["content"]}
        if resp["tool_calls"]:
            asst["tool_calls"] = [
                {"id": tc["id"], "type": "function",
                 "function": {"name": tc["name"],
                              "arguments": json.dumps(tc["arguments"],
                                                      ensure_ascii=False)}}
                for tc in resp["tool_calls"]
            ]
        run["messages"].append(asst)

        if not resp["tool_calls"]:
            # 反"只说不做"骤停:首轮零工具且像铺垫 → 自动 nudge 一次
            if (not run.get("nudged_this_turn")
                    and _looks_like_preamble(resp["content"])
                    and not _has_tools_since_last_user(run["messages"])):
                run["nudged_this_turn"] = True
                run["messages"].append({
                    "role": "user",
                    "content": ("请直接调用工具开始,不要只说『我会先…』"
                                "『让我先…』就停下。"),
                })
                yield _rec(run, {"type": "info",
                                 "content": "(检测到只铺垫未动手,已自动续一刀)"})
                continue
            run["status"] = "done"
            if resp["content"]:
                yield _rec(run, {"type": "answer",
                                 "content": resp["content"]})
            yield _rec(run, {"type": "done"})
            _persist(rid)
            return

        run["batch"] = resp["tool_calls"]
        run["bi"] = 0
        # 回到循环顶部处理新 batch


async def stream_start(task: str, web: bool = True) -> AsyncIterator[bytes]:
    rid, err = start_run(task, web=web)
    if err:
        yield _sse({"type": "error", "error": err})
        return
    yield _sse({"type": "run", "run_id": rid})
    # 用户首条指令（transcript 已在 start_run 写入，这里仅推送给前端）
    yield _sse({"type": "user", "content": task})
    extras = RUNS.get(rid, {}).get("extra_paths") or []
    if extras:
        yield _rec(RUNS[rid], {"type": "info",
                                "content": "本轮临时授权访问: "
                                           + ", ".join(extras)})
    async for ev in _drive(rid):
        yield ev


async def stream_respond(
    rid: str, approve: bool, edited_args: dict | None
) -> AsyncIterator[bytes]:
    if not _ensure_loaded(rid):
        yield _sse({"type": "error", "error": "会话不存在或已过期"})
        return
    run = RUNS[rid]
    if run["status"] != "awaiting" or run["bi"] >= len(run["batch"]):
        yield _sse({"type": "error", "error": "当前没有待确认操作"})
        return
    c = run["batch"][run["bi"]]
    c["_decided"] = True
    if not approve:
        c["_denied"] = True
    elif edited_args:
        c["arguments"] = edited_args
    run["status"] = "running"
    async for ev in _drive(rid):
        yield ev


async def stream_continue(rid: str, task: str,
                            web: bool = True) -> AsyncIterator[bytes]:
    """同一 run 追加一轮指令，复用上下文与会话起点检查点（迭代式）。"""
    if not _ensure_loaded(rid):
        yield _sse({"type": "error",
                    "error": "会话不存在或已过期，请新建会话"})
        return
    run = RUNS[rid]
    if run["status"] == "awaiting":
        yield _sse({"type": "error",
                    "error": "有待确认的高危操作，请先批准/拒绝再继续"})
        return
    extras = _extract_paths(task)
    augmented = _augment_task_with_extras(task, extras)
    run["messages"].append({"role": "user", "content": augmented})
    yield _rec(run, {"type": "user", "content": task})
    run["batch"] = []
    run["bi"] = 0
    run["status"] = "running"
    run["nudged_this_turn"] = False
    run["web_on"] = bool(web)
    run["extra_paths"] = extras
    set_extra_paths(extras)
    if extras:
        yield _rec(run, {"type": "info",
                          "content": "本轮临时授权访问: "
                                     + ", ".join(extras)})
    _persist(rid)
    async for ev in _drive(rid):
        yield ev


def rollback(rid: str) -> dict:
    if not _ensure_loaded(rid):
        return {"error": "会话不存在或已过期"}
    run = RUNS[rid]
    if not run.get("checkpoint"):
        return {"error": "无可回滚的检查点"}
    return run_tool("git_rollback", {"to": run["checkpoint"]})
