"""Agent 会话:ReAct 循环 + 高危确认(两态)+ 临时授权 + 检查点 + 取消 + 持久化。

事件为 **dict 流**(传输编码如 SSE 是宿主的事);事件 type:
user / info / checkpoint / tool / result / todos / confirm / answer /
done / error / cancelled。

高危确认两态(同一循环,一个分支):
- 默认(on_confirm=None,"断开式"):遇确认 → 吐 confirm 事件后**结束本段流**,
  status=awaiting;调 respond(approve) 续跑。配合 Store,确认等待期间进程
  重启也能载回继续(batch/bi 一并落盘 —— 旧版重启后确认中会话会变砖)。
- 传 on_confirm 异步回调("单流式"):遇确认 → 吐 confirm 事件后 await 回调,
  按返回 bool 继续,一条流到底。适合 CLI / 简单宿主。

其余可注入/可开关:checkpoint(git 安全网,默认开,**懒打**——本轮第一次改
动前才打、一轮一个,纯聊天不打;非 git 目录可关)、
nudge(反"只说不做"自动续刀,默认开)、exclude_tools(按轮隐藏工具,如
联网开关)。
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, AsyncIterator, Awaitable, Callable

from .confirm import MUTATING_PATH_ARGS, RiskyConfirmPolicy
from .store import MemoryStore
from .tools import ToolRegistry
from .types import ConfirmPolicy, Provider, SessionStore

# Windows 绝对路径 C:\foo\bar 或 D:/baz;从用户消息抽取做临时授权
_ABSPATH_RE = re.compile(r"[A-Za-z]:[\\/](?:[^\s'\"<>|?*\n]+)")
_TRIM = ".,;:!?)】」』\"'“”‘’《》)"

_PREAMBLE_HEADS = ("我们先", "我先", "我会", "让我先", "让我", "先来",
                   "先看", "先定位", "先分析", "先检查", "先读")

# 「改动类」工具(复用 RootConfirmPolicy 同一份清单,自动同步):本轮**第一次**
# 执行其中之一前,自动打一个 git 检查点(懒打)。纯聊天/只读轮不含这些工具 →
# 不打点;一轮最多一个点。撤销 = git reset --hard 到该点 = 撤掉这一整轮的改动。
MUTATING_TOOLS = frozenset(MUTATING_PATH_ARGS)

DEFAULT_SYSTEM_PROMPT = (
    "你是一个本地编程/文件 Agent,只能在授权范围内操作。用提供的工具完成"
    "任务:先用只读工具(list_dir/read_file/search_text)了解情况,再做最小"
    "且精确的改动(edit_file 优先于 write_file)。一次只调用一个工具。"
    "【铁律】禁止只输出『我们先来…』『我会先…』这种铺垫文字然后停下——"
    "要么立刻调用工具开始干活,要么任务真完成了再用简洁中文总结。"
    "当前工作目录:{cwd}"
)

ConfirmHook = Callable[[dict], Awaitable[bool]]


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
    """把临时授权拼到给模型看的 user 消息开头(UI/transcript 用原文)。"""
    if not extras:
        return task
    bullets = "\n".join(f"  - {p}" for p in extras)
    return (
        "[本轮临时授权——以下绝对路径已为本次回答放行,**绝不要**"
        "回答『无权限/不在白名单』,也**绝不要**假定文件不存在;先用"
        "read_file/list_dir/edit_file 等工具实际尝试,工具真的报错了"
        "再据实回报]:\n"
        f"{bullets}\n\n"
        "用户原文:\n" + task
    )


def _looks_like_preamble(content: str | None) -> bool:
    """开头像"我会先/让我先…"且字数短 → 视为只铺垫没动手。"""
    s = (content or "").strip()
    if not s or len(s) > 220:
        return False
    head = s[:30]
    return any(kw in head for kw in _PREAMBLE_HEADS)


def _has_tools_since_last_user(msgs: list[dict]) -> bool:
    last_user_idx = -1
    for i in range(len(msgs) - 1, -1, -1):
        if msgs[i].get("role") == "user":
            last_user_idx = i
            break
    if last_user_idx < 0:
        return False
    return any(m.get("role") == "tool" for m in msgs[last_user_idx:])


class AgentSession:
    def __init__(
        self,
        *,
        provider: Provider | Callable[[], Provider],
        registry: ToolRegistry,
        confirm_policy: ConfirmPolicy | None = None,
        store: SessionStore | None = None,
        system_prompt: str = "",
        session_id: str | None = None,
        checkpoint: bool = True,
        nudge: bool = True,
        on_confirm: ConfirmHook | None = None,
        context_hook: Callable[[str, dict, dict], str | None] | None = None,
    ) -> None:
        self.registry = registry
        self.confirm = confirm_policy or RiskyConfirmPolicy()
        self.store = store or MemoryStore()
        self._provider = provider  # Provider 实例,或每次 drive 重解析的工厂
        self.id = session_id or uuid.uuid4().hex[:12]
        self.on_confirm = on_confirm
        self.checkpoint_enabled = checkpoint
        self.nudge_enabled = nudge
        # 通用上下文钩子:每次工具执行后回调 (name, args, result),返回的文本
        # 拼到该工具结果末尾给模型看。引擎不关心它做什么(宿主用来按需注入
        # 子目录指令、路径规则等);默认 None=无行为,保持库通用。
        self.context_hook = context_hook

        sys_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT.format(
            cwd=registry.scope.cwd)
        self.messages: list[dict] = [
            {"role": "system", "content": sys_prompt}]
        self.transcript: list[dict] = []
        self.status = "ready"  # ready|running|awaiting|done|error|cancelled
        self.title = ""
        self.meta: dict[str, Any] = {}
        self.checkpoints: list[str] = []  # 本会话已打的检查点 commit(按时间)
        self._batch: list[dict] = []
        self._bi = 0
        self._exclude: set[str] = set()
        self._nudged = False
        self._cancelled = False
        self._turn_checkpointed = False  # 本轮是否已打过点(懒打,一轮一个)

    # ---------- 持久化 ----------

    @classmethod
    def load(
        cls,
        session_id: str,
        *,
        provider: Provider | Callable[[], Provider],
        registry: ToolRegistry,
        store: SessionStore,
        confirm_policy: ConfirmPolicy | None = None,
        checkpoint: bool = True,
        nudge: bool = True,
        on_confirm: ConfirmHook | None = None,
        context_hook: Callable[[str, dict, dict], str | None] | None = None,
    ) -> "AgentSession | None":
        """从 Store 载回会话(进程重启后续跑)。没有则返回 None。
        awaiting 状态连同待确认 batch 一起恢复,respond 仍可用。"""
        data = store.load(session_id)
        if not data:
            return None
        s = cls(provider=provider, registry=registry,
                confirm_policy=confirm_policy, store=store,
                session_id=session_id, checkpoint=checkpoint, nudge=nudge,
                on_confirm=on_confirm, context_hook=context_hook)
        s.messages = list(data.get("messages") or s.messages)
        s.transcript = list(data.get("transcript") or [])
        s.status = data.get("status", "done")
        s.title = data.get("title", "")
        s.meta = dict(data.get("meta") or {})
        cps = data.get("checkpoints")
        if cps is None:  # 兼容旧档:单个 checkpoint 标量 → 收成列表
            old = data.get("checkpoint")
            cps = [old] if old else []
        s.checkpoints = list(cps)
        s._batch = list(data.get("batch") or [])
        s._bi = int(data.get("bi") or 0)
        # 与 batch/bi 同属「本轮进行中」状态:awaiting 期间重启续跑要恢复它,
        # 否则同一轮会重复打检查点(违反一轮一个)。
        s._turn_checkpointed = bool(data.get("turn_checkpointed"))
        s._exclude = set(data.get("exclude_tools") or [])
        registry.todos = list(data.get("todos") or [])
        return s

    def _persist(self) -> None:
        self.store.save(self.id, {
            "title": self.title or "(未命名)",
            "cwd": self.registry.scope.cwd,
            "checkpoints": self.checkpoints,
            "checkpoint_enabled": self.checkpoint_enabled,
            "messages": self.messages,
            "transcript": self.transcript,
            "status": self.status,
            "todos": self.registry.todos,
            "batch": self._batch,
            "bi": self._bi,
            "turn_checkpointed": self._turn_checkpointed,
            "exclude_tools": sorted(self._exclude),
            "meta": self.meta,
        })

    def _rec(self, obj: dict) -> dict:
        """记录事件到 transcript(持久化/重载渲染用)并原样返回。"""
        self.transcript.append(obj)
        return obj

    # ---------- 公共入口 ----------

    async def run(self, task: str, *,
                  exclude_tools: set[str] | None = None
                  ) -> AsyncIterator[dict]:
        """开始首轮任务。事件 dict 流;断开式下遇高危确认本段流结束。"""
        if self.status != "ready":
            yield {"type": "error",
                   "error": "会话已开始,请用 continue_/respond"}
            return
        async for ev in self._begin_turn(task, exclude_tools):
            yield ev

    async def run_from(self, messages: list[dict], *,
                       exclude_tools: set[str] | None = None
                       ) -> AsyncIterator[dict]:
        """以**宿主维护的全量历史**开跑一轮(不追加 user 消息、不做临时
        授权抽取)。适合无状态宿主:每轮把完整对话历史传进来,如文件助手
        模式(前端持有历史)。"""
        if self.status != "ready":
            yield {"type": "error",
                   "error": "会话已开始,请用 continue_/respond"}
            return
        self.messages = [self.messages[0]] + [dict(m) for m in messages]
        self._batch = []
        self._bi = 0
        self._exclude = set(exclude_tools or ())
        self._nudged = False
        self._cancelled = False
        self._turn_checkpointed = False
        self.status = "running"
        self._persist()
        async for ev in self._drive():
            yield ev

    async def continue_(self, task: str, *,
                        exclude_tools: set[str] | None = None
                        ) -> AsyncIterator[dict]:
        """同一会话追加一轮指令,复用上下文与会话起点检查点。"""
        if self.status == "awaiting":
            yield {"type": "error",
                   "error": "有待确认的高危操作,请先批准/拒绝再继续"}
            return
        async for ev in self._begin_turn(task, exclude_tools):
            yield ev

    async def respond(self, approve: bool,
                      edited_args: dict | None = None
                      ) -> AsyncIterator[dict]:
        """对断开式确认作出决定并续跑。"""
        if self.status != "awaiting" or self._bi >= len(self._batch):
            yield {"type": "error", "error": "当前没有待确认操作"}
            return
        c = self._batch[self._bi]
        c["_decided"] = True
        if not approve:
            c["_denied"] = True
        elif edited_args:
            c["arguments"] = edited_args
        self.status = "running"
        async for ev in self._drive():
            yield ev

    def cancel(self) -> None:
        """请求取消:置标志 + 立刻终止正在跑的子进程。
        循环在下一个检查点吐 cancelled 事件退出。"""
        self._cancelled = True
        self.registry.kill_running()

    # ---------- 内部 ----------

    async def _begin_turn(self, task: str,
                          exclude_tools: set[str] | None
                          ) -> AsyncIterator[dict]:
        extras = _extract_paths(task)
        # 只对"确实需要放行"的路径做临时授权提示(全盘 Scope 下本就可达,
        # 不发提示、也不给模型拼授权前言,避免噪音)
        needs_grant = [p for p in extras
                       if not self.registry.scope.is_allowed(p)]
        self.registry.scope.grant_temporary(extras)  # 覆盖式:每轮一批
        augmented = _augment_task_with_extras(task, needs_grant)
        self.messages.append({"role": "user", "content": augmented})
        if not self.title:
            self.title = task.strip()[:40] or "(未命名)"
        yield self._rec({"type": "user", "content": task})
        if needs_grant:
            yield self._rec({"type": "info",
                             "content": "本轮临时授权访问: "
                                        + ", ".join(needs_grant)})
        self._batch = []
        self._bi = 0
        self._exclude = set(exclude_tools or ())
        self._nudged = False
        self._cancelled = False
        self._turn_checkpointed = False
        self.status = "running"
        self._persist()
        async for ev in self._drive():
            yield ev

    def _resolve_provider(self) -> Provider:
        p = self._provider
        if hasattr(p, "tool_complete"):
            return p  # type: ignore[return-value]
        return p()  # 工厂:每次 drive 重解析(配置可能变了)

    async def _drive(self) -> AsyncIterator[dict]:
        try:
            prov = self._resolve_provider()
        except Exception as e:  # noqa: BLE001
            self.status = "error"
            yield self._rec({"type": "error", "error": str(e)})
            self._persist()
            return

        # 检查点改为「懒打」:见下方循环——本轮第一次执行改动类工具前才打,
        # 纯聊天/只读轮不打点(不再污染 git 历史,也不再有空检查点)。
        while True:
            if self._cancelled:
                self.status = "cancelled"
                yield self._rec({"type": "cancelled"})
                self._persist()
                return

            # 1) 处理当前 batch 里未完成的 tool_call
            while self._bi < len(self._batch):
                c = self._batch[self._bi]
                # 被排除的工具 = **禁止执行**(不只是不告诉模型)。模型若凭直觉
                # 调了未 advertise 的工具(被隐藏的 git_checkpoint/git_rollback、
                # 或联网关闭时的 web_search),直接挡掉并回报,绝不真执行。
                if c["name"] in self._exclude:
                    err = {"error": f"工具不可用(未启用):{c['name']}"}
                    yield self._rec({"type": "result", "name": c["name"],
                                     "result": err})
                    self.messages.append({
                        "role": "tool", "tool_call_id": c["id"],
                        "content": json.dumps(err, ensure_ascii=False)})
                    self._bi += 1
                    continue
                if (self.confirm.needs_confirm(
                        c["name"], self.registry.is_high_risk(c["name"]),
                        args=c["arguments"])
                        and not c.get("_decided")):
                    yield self._rec({"type": "confirm", "tool": c["name"],
                                     "args": c["arguments"],
                                     "call_id": c["id"]})
                    if self.on_confirm is None:
                        # 断开式:结束本段流,等 respond
                        self.status = "awaiting"
                        self._persist()
                        return
                    # 单流式:await 宿主回调,按结果继续
                    approved = await self.on_confirm(
                        {"tool": c["name"], "args": c["arguments"],
                         "call_id": c["id"]})
                    c["_decided"] = True
                    if not approved:
                        c["_denied"] = True
                if self._cancelled:
                    self.status = "cancelled"
                    yield self._rec({"type": "cancelled"})
                    self._persist()
                    return
                if c.get("_denied"):
                    content = ("用户拒绝执行该操作。请换一种安全的方式"
                               "或询问用户。")
                else:
                    # 懒检查点:本轮第一次真正执行「改动类」工具前,打一个
                    # git 点(确认通过、即将执行时才打;被拒的操作不打)。
                    # 纯聊天/只读轮永远进不到这里 → 不打点;一轮最多一个。
                    if (self.checkpoint_enabled
                            and not self._turn_checkpointed
                            and c["name"] in MUTATING_TOOLS):
                        self._turn_checkpointed = True  # 先置位:失败也不重试
                        cp = self.registry.run(
                            "git_checkpoint", {"message": "cogito 检查点"})
                        if "error" in cp:
                            yield self._rec({
                                "type": "info",
                                "content": "⚠️ 打 git 检查点失败,本轮改动"
                                           "没有回滚点:" + cp["error"]})
                        else:
                            self.checkpoints.append(cp["checkpoint"])
                            yield self._rec({"type": "checkpoint",
                                             "commit": cp["checkpoint"]})
                    yield self._rec({"type": "tool", "name": c["name"],
                                     "args": c["arguments"]})
                    res = self.registry.run(c["name"], c["arguments"])
                    content = json.dumps(res, ensure_ascii=False)[:8000]
                    if self.context_hook:
                        try:
                            extra = self.context_hook(
                                c["name"], c["arguments"], res)
                        except Exception:  # noqa: BLE001
                            extra = None
                        if extra:
                            content = content + "\n\n" + extra
                    yield self._rec({"type": "result", "name": c["name"],
                                     "result": res})
                    if c["name"] in ("todo_set", "todo_update") and \
                            isinstance(res, dict) and "todos" in res:
                        yield self._rec({"type": "todos",
                                         "items": res["todos"]})
                self.messages.append({
                    "role": "tool", "tool_call_id": c["id"],
                    "content": content,
                })
                self._bi += 1

            # 2) batch 处理完,问模型下一步。provider 有流式能力则边走边吐
            #    delta/reasoning 增量事件(瞬时,不进 transcript),否则整轮拿。
            specs = self.registry.specs(exclude=self._exclude)
            streamer = getattr(prov, "stream_tool_complete", None)
            try:
                if streamer is None:
                    resp = await prov.tool_complete(self.messages, specs)
                else:
                    resp = None
                    async for kind, payload in streamer(self.messages, specs):
                        if kind == "answer":
                            yield {"type": "delta", "content": payload}
                        elif kind == "reasoning":
                            yield {"type": "reasoning", "content": payload}
                        elif kind == "final":
                            resp = payload
                        if self._cancelled:
                            break
                    if resp is None and not self._cancelled:
                        raise RuntimeError("流式响应未完成(缺 final)")
            except Exception as e:  # noqa: BLE001
                self.status = "error"
                yield self._rec({"type": "error",
                                 "error": f"模型调用失败:{e}"})
                self._persist()
                return

            if self._cancelled:
                self.status = "cancelled"
                yield self._rec({"type": "cancelled"})
                self._persist()
                return

            asst: dict[str, Any] = {"role": "assistant",
                                    "content": resp["content"]}
            if resp["tool_calls"]:
                asst["tool_calls"] = [
                    {"id": tc["id"], "type": "function",
                     "function": {"name": tc["name"],
                                  "arguments": json.dumps(
                                      tc["arguments"], ensure_ascii=False)}}
                    for tc in resp["tool_calls"]
                ]
                # 思考模型(DeepSeek V4 等):带工具调用的轮次必须把思考链一并
                # 回传,否则上游在后续请求里报错。非思考模型无此字段,跳过。
                if resp.get("reasoning_content"):
                    asst["reasoning_content"] = resp["reasoning_content"]
            self.messages.append(asst)

            if not resp["tool_calls"]:
                # 反"只说不做"骤停:首轮零工具且像铺垫 → 自动 nudge 一次
                if (self.nudge_enabled and not self._nudged
                        and _looks_like_preamble(resp["content"])
                        and not _has_tools_since_last_user(self.messages)):
                    self._nudged = True
                    self.messages.append({
                        "role": "user",
                        "content": ("请直接调用工具开始,不要只说"
                                    "『我会先…』『让我先…』就停下。"),
                    })
                    yield self._rec({"type": "info",
                                     "content": "(检测到只铺垫未动手,"
                                                "已自动续一刀)"})
                    continue
                self.status = "done"
                if resp["content"]:
                    yield self._rec({"type": "answer",
                                     "content": resp["content"]})
                yield self._rec({"type": "done"})
                self._persist()
                return

            self._batch = resp["tool_calls"]
            self._bi = 0
            # 回到循环顶部处理新 batch
