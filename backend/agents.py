"""装配层:把 cogito_engine 接到本应用(配置/持久化/搜索/skills/SSE)。

统一会话(对话=Agent,Claude Code 式):
- 每个会话绑一个**根目录**(创建时快照,默认=设置里的工作目录);
- **C 边界模型**:读/搜全盘自动(AllowAllScope);写/删/命令在根外
  **永远确认**(引擎 RootConfirmPolicy),根内按设置页确认档
  (ConfigConfirmPolicy,实时读 settings);
- git 安全网自动:根目录是 git 仓库 → 开检查点/回滚;不是 → 自动关,
  开场提示一句;
- 工具常驻(FS/exec/git/todo/user_dirs + web_search),纯聊天=零工具轮;
- 引擎 dict 事件 → SSE 字节(传输编码是宿主的事)。
"""

from __future__ import annotations

import json
import os
from typing import Any, AsyncIterator

import config
import store as appstore
from cogito_engine import (
    AgentSession,
    AllowAllScope,
    RootConfirmPolicy,
    ToolRegistry,
)
from cogito_engine import userdirs
from cogito_engine.providers import ProviderError
from llm import build_provider
from skills_loader import build_injection


def _sse(obj: dict) -> bytes:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode("utf-8")


class ConfigConfirmPolicy:
    """根内的确认档:实时读 settings(all/risky/none + 不可逆清单)。"""

    def needs_confirm(self, tool_name: str, high_risk: bool,
                      args: dict | None = None) -> bool:
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


_SYS_UNIFIED = (
    "你是 Cogito,一个跑在用户本机的 AI 助手 + 编程/文件 Agent。"
    "你以一个**根目录**为工作基地(当前:{cwd}),用提供的工具完成任务;"
    "纯聊天/问答不需要工具,直接回答即可。"
    "【工作方式】干活先用只读工具(list_dir/read_file/search_text)了解"
    "情况,再做最小且精确的改动(edit_file 优先于 write_file);改完代码"
    "调用 run_tests 验证。一次只调用一个工具。"
    "凡是涉及第三方库/框架/API 的用法、版本差异、报错信息、或任何你"
    "不确定的最新信息,务必先调用 web_search 查资料再动手,不要凭记忆硬写。"
    "【访问边界】读/搜索全盘自由(绝对路径即可,如桌面/下载等用 user_dirs "
    "拿真实路径,别猜);但**写/删/跑命令**发生在根目录之外时会弹给用户"
    "确认——这是预期行为,直接调用工具即可,绝不要回答『无权限/不在"
    "白名单』。不要假定文件不存在:先用工具实际尝试,真报错了再据实回报。"
    "{dirs}"
    "【铁律】禁止只输出『我们先来…』『我会先…』这种铺垫文字然后停下——"
    "要么立刻调用工具,要么任务完成后用简洁中文给最终总结。"
    "**只用纯文本回复 = 本次响应结束**,不要当成『准备动手』的开场白。"
    "【待办清单】对非简单任务(超过 2 步),开局第一个工具调用就是 "
    "todo_set,列 3-8 项干练标题;每开始一项 todo_update 改 in_progress,"
    "完成立刻改 completed。简单任务免列。"
    "{git_note}"
)


def _system_prompt(cwd: str, has_git: bool) -> str:
    git_note = (
        "【安全网】会话开始已打 git 检查点,用户可一键回滚你的改动。"
        if has_git else
        "【注意】当前根目录不是 git 仓库,没有检查点/回滚安全网,"
        "改动文件时务必加倍谨慎、改前确认理解正确。"
    )
    sys_content = _SYS_UNIFIED.format(
        cwd=cwd, dirs=userdirs.prompt_hint(), git_note=git_note)
    skills = build_injection(config.get_skills_enabled())
    if skills:
        sys_content = skills + "\n\n" + sys_content
    return sys_content


def _registry(cwd: str) -> ToolRegistry:
    ws = config.get_workspace()
    reg = ToolRegistry(AllowAllScope(cwd=cwd),
                       test_cmd=ws.get("test_cmd", ""))
    reg.register("web_search", _web_search_tool,
                 spec=_WEB_SEARCH_SPEC, high_risk=False)
    return reg


def _confirm_policy(cwd: str) -> RootConfirmPolicy:
    return RootConfirmPolicy(cwd, inner=ConfigConfirmPolicy())


# 活跃会话缓存(内存);不在则从 JSON 载回(后端重启后可继续)
_AGENT_SESSIONS: dict[str, AgentSession] = {}


def _get_agent_session(rid: str) -> AgentSession | None:
    s = _AGENT_SESSIONS.get(rid)
    if s is not None:
        return s
    data = appstore.get_agent(rid)
    if not data:
        return None
    cwd = data.get("cwd") or config.get_workspace().get("cwd") or os.getcwd()
    s = AgentSession.load(
        rid, provider=_tool_provider, registry=_registry(cwd),
        store=_AGENT_STORE, confirm_policy=_confirm_policy(cwd),
        # 原会话有检查点才继续启用;当初没有(非 git)就保持关
        checkpoint=bool(data.get("checkpoint")),
    )
    if s is not None:
        _AGENT_SESSIONS[rid] = s
    return s


def _web_exclude(web: bool) -> set[str] | None:
    return None if web else {"web_search"}


async def agent_stream_start(task: str, web: bool = True
                             ) -> AsyncIterator[bytes]:
    cwd = (config.get_workspace().get("cwd") or "").strip()
    if not cwd or not os.path.isdir(cwd):
        yield _sse({"type": "error",
                    "error": "未设置有效的根目录(设置页选当前工作目录,"
                             "或在顶部下拉切换)"})
        return
    has_git = os.path.isdir(os.path.join(cwd, ".git"))
    session = AgentSession(
        provider=_tool_provider, registry=_registry(cwd),
        confirm_policy=_confirm_policy(cwd), store=_AGENT_STORE,
        system_prompt=_system_prompt(cwd, has_git), checkpoint=has_git,
    )
    _AGENT_SESSIONS[session.id] = session
    yield _sse({"type": "run", "run_id": session.id})
    if not has_git:
        yield _sse({"type": "info",
                    "content": "根目录不是 git 仓库 —— 本会话没有检查点/"
                               "回滚安全网(可在顶部初始化 git)"})
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


def agent_stop(rid: str) -> bool:
    """停止会话:置 cancelled + 立刻 kill 正在跑的子进程。"""
    s = _AGENT_SESSIONS.get(rid)
    if s is None:
        return False
    s.cancel()
    return True


def agent_rollback(rid: str) -> dict:
    s = _get_agent_session(rid)
    if s is None:
        return {"error": "会话不存在或已过期"}
    if not s.checkpoint_commit:
        return {"error": "无可回滚的检查点"}
    return s.registry.run("git_rollback", {"to": s.checkpoint_commit})
