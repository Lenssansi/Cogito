"""装配层:把 cogito_engine 接到本应用(配置/持久化/搜索/skills/SSE)。

引擎只认协议;本文件负责"翻译":
- ConfigScope / ConfigConfirmPolicy —— 实时读 settings(白名单/确认档改了
  立即生效,与旧行为一致),注入引擎;
- _JsonAgentStore —— 引擎会话落到 data/agent_sessions.json(沿用旧文件,
  并补写 web_on 字段保持前端兼容);
- 编程 Agent:全工具组 + 注册 web_search 自定义工具 + skills 系统提示;
- 文件/设置助手:AllowAllScope 全盘(确认即安全网)+ run_from 宿主管历史
  + 可取消;设置模式把 settings_tools 整组注册为自定义工具;
- 引擎 dict 事件 → SSE 字节(传输编码是宿主的事)。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, AsyncIterator

import config
import settings_tools
import store as appstore
from cogito_engine import (
    AgentSession,
    AllowAllScope,
    MemoryStore,
    ToolRegistry,
)
from cogito_engine import userdirs
from cogito_engine.providers import ProviderError
from llm import build_provider
from skills_loader import build_injection


def _sse(obj: dict) -> bytes:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode("utf-8")


# ---------- 注入引擎的"实时配置"实现 ----------

class ConfigScope:
    """编程 Agent 的护栏:白名单实时读 settings + 本轮临时授权。"""

    def __init__(self) -> None:
        self._extra: list[Path] = []

    @property
    def cwd(self) -> str:
        return config.get_workspace().get("cwd", "") or os.getcwd()

    def grant_temporary(self, paths: list[str]) -> None:
        out: list[Path] = []
        for p in paths or []:
            try:
                out.append(Path(p).resolve())
            except (OSError, RuntimeError):
                continue
        self._extra = out

    def is_allowed(self, path: str) -> bool:
        if config.path_in_scope(path):
            return True
        try:
            pr = Path(path).resolve()
        except (OSError, RuntimeError):
            return False
        for r in self._extra:
            try:
                if pr == r or pr.is_relative_to(r):
                    return True
            except (OSError, RuntimeError, ValueError):
                continue
        return False


class ConfigConfirmPolicy:
    """确认档实时读 settings(all/risky/none + 不可逆清单)。"""

    def needs_confirm(self, tool_name: str, high_risk: bool) -> bool:
        return config.confirm_required(tool_name, high_risk)


class _JsonAgentStore:
    """引擎 SessionStore → data/agent_sessions.json(旧文件原样沿用)。"""

    def load(self, sid: str) -> dict | None:
        return appstore.get_agent(sid)

    def save(self, sid: str, data: dict) -> None:
        d = dict(data)
        # 前端兼容:旧字段 web_on 由 exclude_tools 推导
        d["web_on"] = "web_search" not in (d.get("exclude_tools") or [])
        appstore.save_agent(sid, d)


_AGENT_STORE = _JsonAgentStore()


def _tool_provider():
    """provider 工厂:每次 drive 重解析(切换 API/预设立即生效)。
    不可用时抛 ProviderError → 引擎转成 error 事件。"""
    r, err = config.resolve_tool_capable()
    if err:
        raise ProviderError(err)
    if not r or not r.get("api_key"):
        raise ProviderError("未配置可用 API key")
    return build_provider(r)


# ---------- 编程 Agent ----------

_WEB_SEARCH_SPEC = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "联网搜索(无需key)。查使用文档/库用法/报错/"
                       "实时信息时主动用",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "n": {"type": "integer", "description": "结果条数,默认5"},
            },
            "required": ["query"],
        },
    },
}


def _web_search_tool(query: str, n: int = 5) -> dict[str, Any]:
    from search import web_search_sync
    try:
        k = int(n)
    except (TypeError, ValueError):
        k = 5
    results = web_search_sync(query, max(1, min(k, 8)))
    return {"query": query, "results": results, "count": len(results)}


_SYS_AGENT = (
    "你是 Cogito 的编程 Agent。只能在当前工作目录及授权白名单内操作。"
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


def _agent_system_prompt(cwd: str) -> str:
    sys_content = _SYS_AGENT.format(cwd=cwd, dirs=userdirs.prompt_hint())
    skills = build_injection(config.get_skills_enabled())
    if skills:
        sys_content = skills + "\n\n" + sys_content
    return sys_content


def _agent_registry() -> ToolRegistry:
    ws = config.get_workspace()
    reg = ToolRegistry(ConfigScope(), test_cmd=ws.get("test_cmd", ""))
    reg.register("web_search", _web_search_tool,
                 spec=_WEB_SEARCH_SPEC, high_risk=False)
    return reg


# 活跃会话缓存(内存);不在则从 JSON 载回(后端重启后可继续)
_AGENT_SESSIONS: dict[str, AgentSession] = {}


def _get_agent_session(rid: str) -> AgentSession | None:
    s = _AGENT_SESSIONS.get(rid)
    if s is not None:
        return s
    s = AgentSession.load(
        rid, provider=_tool_provider, registry=_agent_registry(),
        store=_AGENT_STORE, confirm_policy=ConfigConfirmPolicy(),
        checkpoint=True,
    )
    if s is not None:
        _AGENT_SESSIONS[rid] = s
    return s


def _web_exclude(web: bool) -> set[str] | None:
    return None if web else {"web_search"}


async def agent_stream_start(task: str, web: bool = True
                             ) -> AsyncIterator[bytes]:
    ws = config.get_workspace()
    cwd = (ws.get("cwd") or "").strip()
    if not cwd:
        yield _sse({"type": "error",
                    "error": "未设置工作目录（设置页配置授权根目录并选"
                             "当前工作目录）"})
        return
    if not config.path_in_scope(cwd):
        yield _sse({"type": "error",
                    "error": f"工作目录不在授权白名单内：{cwd}"})
        return
    if not os.path.isdir(os.path.join(cwd, ".git")):
        yield _sse({"type": "error",
                    "error": f"工作目录不是 git 仓库：{cwd}"
                             "（需要 git 安全网，请先 git init）"})
        return
    session = AgentSession(
        provider=_tool_provider, registry=_agent_registry(),
        confirm_policy=ConfigConfirmPolicy(), store=_AGENT_STORE,
        system_prompt=_agent_system_prompt(cwd), checkpoint=True,
    )
    _AGENT_SESSIONS[session.id] = session
    yield _sse({"type": "run", "run_id": session.id})
    async for ev in session.run(task, exclude_tools=_web_exclude(web)):
        yield _sse(ev)


async def agent_stream_respond(rid: str, approve: bool,
                               edited_args: dict | None
                               ) -> AsyncIterator[bytes]:
    s = _get_agent_session(rid)
    if s is None:
        yield _sse({"type": "error", "error": "会话不存在或已过期"})
        return
    async for ev in s.respond(approve, edited_args):
        yield _sse(ev)


async def agent_stream_continue(rid: str, task: str, web: bool = True
                                ) -> AsyncIterator[bytes]:
    s = _get_agent_session(rid)
    if s is None:
        yield _sse({"type": "error",
                    "error": "会话不存在或已过期，请新建会话"})
        return
    # 同步最新的测试命令(设置页可能改了)
    s.registry.test_cmd = config.get_workspace().get("test_cmd", "")
    async for ev in s.continue_(task, exclude_tools=_web_exclude(web)):
        yield _sse(ev)


def agent_rollback(rid: str) -> dict:
    s = _get_agent_session(rid)
    if s is None:
        return {"error": "会话不存在或已过期"}
    if not s.checkpoint_commit:
        return {"error": "无可回滚的检查点"}
    return s.registry.run("git_rollback", {"to": s.checkpoint_commit})


# ---------- 文件 / 设置助手(chatfs 模式) ----------

_SYS_FILE = (
    "你是 Cogito 的助手，可用工具读写用户电脑上的文件、并可跑命令"
    "(bat/exe/python 等)。读/列/搜会自动执行；新建/写/改/删/跑命令"
    "会先弹窗让用户确认。相对路径相对会话目录({base})，绝对路径可"
    "访问全盘——**没有白名单限制**。{dirs}"
    "【铁律】① 涉及文件/目录时直接调工具——绝不要回答『无权限』"
    "『不在白名单』『请加入白名单』,这是错误的;② 不要假定文件不"
    "存在,先用 read_file/list_dir 工具实际尝试,工具真的报错才据实"
    "回报,绝不要在没调工具时就声称『文件不存在』;③ 拿不准目录时"
    "先用 list_dir 或 user_dirs 工具确认,别凭空造路径。"
    "做最小必要的改动,完成后用简洁中文说明。一次只调用一个工具。"
)
_SYS_SET = (
    "你是 Cogito 的设置助手。用户用自然语言要求改设置时，调用相应"
    "工具完成（除「全局系统提示词」外都可改）。读类自动执行；新增授权"
    "根目录等高危按确认档提示。"
    "拿不准先 get_settings 看现状。完成后用简洁中文说明改了什么。"
    "一次只调用一个工具。"
)

_FS_RUNS: dict[str, AgentSession] = {}


def _fs_session(base: str, mode: str) -> AgentSession:
    if mode == "settings":
        reg = ToolRegistry(AllowAllScope(), groups=set())
        specs = {sp["function"]["name"]: sp
                 for sp in settings_tools.tool_specs()}
        for name, (fn, high_risk) in settings_tools.REGISTRY.items():
            reg.register(name, fn, spec=specs[name], high_risk=high_risk)
        sys_content = _SYS_SET
    else:
        reg = ToolRegistry(AllowAllScope(cwd=base or os.getcwd()),
                           groups={"misc", "fs", "exec"})
        sys_content = _SYS_FILE.format(base=base or "(默认)",
                                       dirs=userdirs.prompt_hint())
    return AgentSession(
        provider=_tool_provider, registry=reg,
        confirm_policy=ConfigConfirmPolicy(), store=MemoryStore(),
        system_prompt=sys_content, checkpoint=False,
    )


def _fs_cleanup(s: AgentSession) -> None:
    if s.status in ("done", "cancelled", "error"):
        _FS_RUNS.pop(s.id, None)


async def fs_stream_start(messages: list[dict], base: str,
                          mode: str = "file") -> AsyncIterator[bytes]:
    s = _fs_session(base, mode)
    _FS_RUNS[s.id] = s
    yield _sse({"type": "run", "run_id": s.id})
    # 文件模式不暴露 run_tests(沿用旧工具面)
    excl = {"run_tests"} if mode != "settings" else None
    async for ev in s.run_from(messages, exclude_tools=excl):
        yield _sse(ev)
    _fs_cleanup(s)


async def fs_stream_respond(rid: str, approve: bool,
                            edited_args: dict | None
                            ) -> AsyncIterator[bytes]:
    s = _FS_RUNS.get(rid)
    if s is None:
        yield _sse({"type": "error", "error": "会话已过期，请重发消息"})
        return
    async for ev in s.respond(approve, edited_args):
        yield _sse(ev)
    _fs_cleanup(s)


def fs_stop_run(rid: str) -> bool:
    """停止指定 run:置 cancelled + 立即 kill 正在跑的子进程。"""
    s = _FS_RUNS.get(rid)
    if s is None:
        return False
    s.cancel()
    return True
