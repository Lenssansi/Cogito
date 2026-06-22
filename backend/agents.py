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

import asyncio
import json
import os
from typing import Any, AsyncIterator

import config
import memory
import memory_extract
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


def _register_memory_tools(reg: ToolRegistry, cwd: str) -> None:
    """注册跨会话记忆工具。它们操作 ~/.cogito(绕过项目 scope,属系统能力);
    非高危、不弹确认(写自己的记忆,像 Claude 写 memory 那样)。cwd 绑定本会话
    根目录 → 决定写哪个项目的记忆。"""
    S = {"type": "string"}

    def _save(name: str, description: str, type: str = "project",
              body: str = "") -> dict:
        return memory.save_memory(cwd, name, description, type, body)

    def _update(name: str, description: str = "", type: str = "",
                body: str = "") -> dict:
        return memory.update_memory(cwd, name, description, type, body)

    reg.register("memory_save", _save, high_risk=False, spec={
        "type": "function", "function": {
            "name": "memory_save",
            "description": "记一条跨会话记忆(学到持久且非显然的事才记;已有同"
                           "一事用 memory_update;别记代码/git 里已有的)。",
            "parameters": {"type": "object", "properties": {
                "name": {"type": "string", "description": "kebab-case 短 slug"},
                "description": {"type": "string",
                                "description": "一句话摘要,召回靠它,要写好"},
                "type": {"type": "string",
                         "description": "user|feedback|project|reference"},
                "body": {"type": "string", "description": "事实正文"},
            }, "required": ["name", "description", "body"]}}})
    reg.register("memory_update", _update, high_risk=False, spec={
        "type": "function", "function": {
            "name": "memory_update",
            "description": "更新已有记忆的字段(留空=不改)",
            "parameters": {"type": "object", "properties": {
                "name": S, "description": S, "type": S, "body": S,
            }, "required": ["name"]}}})
    reg.register("memory_delete",
                 lambda name: memory.delete_memory(cwd, name),
                 high_risk=False, spec={
                     "type": "function", "function": {
                         "name": "memory_delete",
                         "description": "删除一条过时/错误的记忆",
                         "parameters": {"type": "object", "properties":
                                        {"name": S}, "required": ["name"]}}})
    reg.register("memory_read",
                 lambda name: memory.read_memory(cwd, name),
                 high_risk=False, spec={
                     "type": "function", "function": {
                         "name": "memory_read",
                         "description": "读一条记忆的全文(索引里看到、要细节时用)",
                         "parameters": {"type": "object", "properties":
                                        {"name": S}, "required": ["name"]}}})


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


def _system_prompt(cwd: str, has_git: bool, task: str = "") -> str:
    git_note = (
        "【安全网】你这一轮第一次改文件/跑命令前,系统会自动打一个 git "
        "检查点;用户可一键回滚你某一轮的改动——放手做,但仍要稳。"
        "(检查点由系统自动管理,你无需、也无法自己打点或回滚。)"
        if has_git else
        "【注意】当前根目录不是 git 仓库,没有检查点/回滚安全网,"
        "改动文件时务必加倍谨慎、改前确认理解正确。"
    )
    sys_content = _SYS_UNIFIED.format(
        cwd=cwd, dirs=userdirs.prompt_hint(), git_note=git_note)
    skills = build_injection(config.get_skills_enabled())
    if skills:
        sys_content = skills + "\n\n" + sys_content
    # 记忆区(静态层 COGITO.md + 纪律 + 动态层索引/召回)追加到末尾
    mem = memory.inject(cwd, task)
    if mem:
        sys_content = sys_content + "\n\n" + mem
    return sys_content


def _registry(cwd: str) -> ToolRegistry:
    ws = config.get_workspace()
    reg = ToolRegistry(AllowAllScope(cwd=cwd),
                       test_cmd=ws.get("test_cmd", ""))
    reg.register("web_search", _web_search_tool,
                 spec=_WEB_SEARCH_SPEC, high_risk=False)
    _register_memory_tools(reg, cwd)
    return reg


def _confirm_policy(cwd: str) -> RootConfirmPolicy:
    return RootConfirmPolicy(cwd, inner=ConfigConfirmPolicy())


# 读类工具看 path 参数:读到子目录文件时,把该目录 COGITO.md + 命中的路径规则
# 拼到工具结果末尾给模型看(静态层的"就近懒加载",对应 Claude Code 子目录 CLAUDE.md)
_READ_TOOLS = {"read_file", "list_dir", "search_text"}


def _make_context_hook(cwd: str):
    loaded: set[str] = set()     # 本会话已注入过的子目录/规则,避免重复

    def hook(name: str, args: dict, result: dict) -> str | None:
        if name not in _READ_TOOLS:
            return None
        return memory.subdir_context(cwd, (args or {}).get("path"), loaded)

    return hook


# ---------- 自动记忆提炼(每轮后,与模型强弱无关地"自建"记忆)----------
_extract_tasks: set = set()


def _last_turn_excerpt(session: AgentSession) -> tuple[str, str, bool]:
    """从 transcript 取最后一轮:原始用户文本 + 最终回答 + 是否用过工具。"""
    tr = session.transcript
    li = -1
    for i in range(len(tr) - 1, -1, -1):
        if tr[i].get("type") == "user":
            li = i
            break
    if li < 0:
        return "", "", False
    seg = tr[li + 1:]
    answer = ""
    for ev in seg:
        if ev.get("type") == "answer":
            answer = ev.get("content") or ""
    used_tools = any(ev.get("type") == "tool" for ev in seg)
    return tr[li].get("content") or "", answer, used_tools


def _schedule_extract(session: AgentSession) -> None:
    """每轮结束后调度后台记忆提炼(过门控才跑;失败绝不影响主流程)。
    强模型已可即时 memory_save,本步是让任何模型都自动建记忆的兜底。"""
    try:
        user_text, answer, used_tools = _last_turn_excerpt(session)
        if not user_text or not memory_extract.worth_extracting(
                user_text, answer, used_tools):
            return
        resolved = config.get_active_resolved()
        if not resolved or not resolved.get("api_key"):
            return
        provider = build_provider(resolved)
        root = session.registry.scope.cwd
        t = asyncio.create_task(
            memory_extract.run(root, user_text, answer, provider))
        _extract_tasks.add(t)            # 持引用防 GC
        t.add_done_callback(_extract_tasks.discard)
    except Exception:  # noqa: BLE001 调度失败绝不影响主流程
        pass


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
        # 原会话开了 git 安全网就继续启用(与是否已打过点无关——懒打下纯聊天
        # 会话当下还没有点,但续跑后真动文件时仍需要)。兼容旧档的标量字段。
        checkpoint=bool(data.get("checkpoint_enabled",
                                 data.get("checkpoint"))),
        context_hook=_make_context_hook(cwd),
    )
    if s is not None:
        _AGENT_SESSIONS[rid] = s
    return s


# 检查点/回滚由引擎(懒打)与 UI(一键回滚)管理,**不暴露给模型**:
# 否则模型自己 git_checkpoint 打的点不进会话检查点列表、回滚会无视它
# (就是 bug ④c);git_rollback 更不该让模型随手 reset --hard。
_HIDDEN_TOOLS = {"git_checkpoint", "git_rollback"}


def _exclude_tools(web: bool) -> set[str]:
    ex = set(_HIDDEN_TOOLS)
    if not web:
        ex.add("web_search")
    return ex


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
        system_prompt=_system_prompt(cwd, has_git, task), checkpoint=has_git,
        context_hook=_make_context_hook(cwd),
    )
    _AGENT_SESSIONS[session.id] = session
    yield _sse({"type": "run", "run_id": session.id})
    if not has_git:
        yield _sse({"type": "info",
                    "content": "根目录不是 git 仓库 —— 本会话没有检查点/"
                               "回滚安全网(可在顶部初始化 git)"})
    async for ev in session.run(task, exclude_tools=_exclude_tools(web)):
        yield _sse(ev)
    if session.status == "done":
        _schedule_extract(session)


async def agent_stream_respond(rid: str, approve: bool,
                               edited_args: dict | None
                               ) -> AsyncIterator[bytes]:
    s = _get_agent_session(rid)
    if s is None:
        yield _sse({"type": "error", "error": "会话不存在或已过期"})
        return
    async for ev in s.respond(approve, edited_args):
        yield _sse(ev)
    if s.status == "done":
        _schedule_extract(s)


async def agent_stream_continue(rid: str, task: str, web: bool = True
                                ) -> AsyncIterator[bytes]:
    s = _get_agent_session(rid)
    if s is None:
        yield _sse({"type": "error",
                    "error": "会话不存在或已过期，请新建会话"})
        return
    # 同步最新的测试命令(设置页可能改了)
    s.registry.test_cmd = config.get_workspace().get("test_cmd", "")
    async for ev in s.continue_(task, exclude_tools=_exclude_tools(web)):
        yield _sse(ev)
    if s.status == "done":
        _schedule_extract(s)


def agent_stop(rid: str) -> bool:
    """停止会话:置 cancelled + 立刻 kill 正在跑的子进程。"""
    s = _AGENT_SESSIONS.get(rid)
    if s is None:
        return False
    s.cancel()
    return True


def agent_rollback(rid: str, to: str | None = None) -> dict:
    """回滚到本会话的某个检查点(git reset --hard,只作用于根目录这个仓库)。

    - 不传 to → 回到**最早**的点(撤掉整段会话对文件的改动);
    - 传了 to → 必须是本会话打过的检查点之一,否则拒绝(防越权 reset 到
      任意 commit / 前端传错)。回滚只动根目录内的 git 仓库,不碰根外。
    """
    s = _get_agent_session(rid)
    if s is None:
        return {"error": "会话不存在或已过期"}
    if not s.checkpoints:
        return {"error": "本会话还没有检查点(纯聊天/没改过文件)"}
    target = to or s.checkpoints[0]
    if target not in s.checkpoints:
        return {"error": "回滚目标不是本会话的检查点,已拒绝"}
    return s.registry.run("git_rollback", {"to": target})
