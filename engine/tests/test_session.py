"""AgentSession 循环测试 —— 喂脚本化假 Provider,全程离线。

覆盖:happy path(工具真的被执行、结果真的回填给模型)/ 高危确认两态
(断开式 respond 续跑 + 单流 on_confirm 回调)/ 拒绝回填 / 临时授权抽取 /
反铺垫 nudge / 检查点可选 / 取消 / 持久化与跨"重启"恢复(含 awaiting
状态带 batch 恢复 —— 修复旧版重启后确认中会话变砖的问题)/ 工具排除。
"""

from __future__ import annotations

import asyncio
import subprocess

from cogito_engine.confirm import RiskyConfirmPolicy
from cogito_engine.scope import DirScope
from cogito_engine.session import AgentSession
from cogito_engine.store import MemoryStore
from cogito_engine.tools import ToolRegistry


class ScriptedProvider:
    """按脚本逐轮返回;记录每轮收到的 messages/tools 供断言。"""

    def __init__(self, script: list[dict]) -> None:
        self.script = list(script)
        self.calls: list[tuple[list[dict], list[dict]]] = []

    async def tool_complete(self, messages, tools):
        self.calls.append(([dict(m) for m in messages], list(tools)))
        return self.script.pop(0)


def _tc(name: str, args: dict, cid: str = "c1") -> dict:
    return {"content": "", "tool_calls": [
        {"id": cid, "name": name, "arguments": args}]}


def _answer(text: str) -> dict:
    return {"content": text, "tool_calls": []}


def _tc_think(name: str, args: dict, reasoning: str, cid: str = "c1") -> dict:
    """思考模式下带 reasoning_content 的工具调用响应。"""
    return {"content": "", "reasoning_content": reasoning,
            "tool_calls": [{"id": cid, "name": name, "arguments": args}]}


def _session(tmp_path, script, **kw) -> tuple[AgentSession, ScriptedProvider]:
    scope = DirScope(cwd=str(tmp_path), allowed_roots=[str(tmp_path)])
    reg = ToolRegistry(scope)
    prov = ScriptedProvider(script)
    kw.setdefault("checkpoint", False)
    s = AgentSession(provider=prov, registry=reg,
                     confirm_policy=RiskyConfirmPolicy("risky"),
                     store=MemoryStore(), **kw)
    return s, prov


def _collect(agen) -> list[dict]:
    async def go():
        return [e async for e in agen]
    return asyncio.run(go())


# ---------- happy path ----------

def test_happy_path_executes_tool_and_feeds_result_back(tmp_path):
    (tmp_path / "a.txt").write_text("内容123", encoding="utf-8")
    s, prov = _session(tmp_path, [
        _tc("read_file", {"path": "a.txt"}),
        _answer("读完了"),
    ])
    events = _collect(s.run("读一下 a.txt"))
    types = [e["type"] for e in events]
    assert types[0] == "user"
    assert "tool" in types and "result" in types
    assert events[-2]["type"] == "answer" and events[-2]["content"] == "读完了"
    assert events[-1]["type"] == "done"
    assert s.status == "done"
    # 第二轮模型收到的 messages 里必须有工具结果(role=tool, 含文件内容)
    second_msgs = prov.calls[1][0]
    tool_msgs = [m for m in second_msgs if m.get("role") == "tool"]
    assert tool_msgs and "内容123" in tool_msgs[0]["content"]


def test_thinking_reasoning_passed_back_with_tool_calls(tmp_path):
    """思考模式 + 工具:带 tool_calls 的 assistant 消息必须回传 reasoning_content
    (DeepSeek V4 硬性要求;缺了第二轮会被上游拒)。非思考模型无此字段,不受影响。"""
    (tmp_path / "a.txt").write_text("hi", encoding="utf-8")
    s, prov = _session(tmp_path, [
        _tc_think("read_file", {"path": "a.txt"}, "我先读这个文件"),
        _answer("done"),
    ])
    _collect(s.run("读 a.txt"))
    second_msgs = prov.calls[1][0]                # 第二轮发回上游的 messages
    asst = [m for m in second_msgs
            if m.get("role") == "assistant" and m.get("tool_calls")]
    assert asst, "应有带 tool_calls 的 assistant 消息"
    assert asst[0].get("reasoning_content") == "我先读这个文件"


# ---------- 高危确认:断开式(默认) ----------

def test_confirm_disconnected_pause_and_respond(tmp_path):
    (tmp_path / "x.txt").write_text("x", encoding="utf-8")
    s, prov = _session(tmp_path, [
        _tc("delete_path", {"path": "x.txt"}),
        _answer("删除完成"),
    ])
    ev1 = _collect(s.run("删掉 x.txt"))
    assert ev1[-1]["type"] == "confirm"          # 流在确认处结束
    assert s.status == "awaiting"
    assert (tmp_path / "x.txt").exists()         # 还没执行

    ev2 = _collect(s.respond(approve=True))
    assert not (tmp_path / "x.txt").exists()     # 批准后真删了
    assert ev2[-1]["type"] == "done"


def test_confirm_disconnected_deny_feeds_refusal(tmp_path):
    (tmp_path / "x.txt").write_text("x", encoding="utf-8")
    s, prov = _session(tmp_path, [
        _tc("delete_path", {"path": "x.txt"}),
        _answer("好的,不删了"),
    ])
    _collect(s.run("删掉 x.txt"))
    _collect(s.respond(approve=False))
    assert (tmp_path / "x.txt").exists()         # 没删
    # 模型在第二轮收到"用户拒绝"的回填
    second_msgs = prov.calls[1][0]
    tool_msgs = [m for m in second_msgs if m.get("role") == "tool"]
    assert tool_msgs and "拒绝" in tool_msgs[0]["content"]


def test_respond_with_edited_args(tmp_path):
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    (tmp_path / "b.txt").write_text("x", encoding="utf-8")
    s, _ = _session(tmp_path, [
        _tc("delete_path", {"path": "a.txt"}),
        _answer("done"),
    ])
    _collect(s.run("删 a"))
    _collect(s.respond(approve=True, edited_args={"path": "b.txt"}))
    assert (tmp_path / "a.txt").exists()         # 用户改了参数:删的是 b
    assert not (tmp_path / "b.txt").exists()


# ---------- 高危确认:单流 on_confirm 回调 ----------

def test_confirm_callback_single_stream_approve(tmp_path):
    (tmp_path / "x.txt").write_text("x", encoding="utf-8")

    async def approve_all(call: dict) -> bool:
        assert call["tool"] == "delete_path"
        return True

    s, _ = _session(tmp_path, [
        _tc("delete_path", {"path": "x.txt"}),
        _answer("删除完成"),
    ], on_confirm=approve_all)
    events = _collect(s.run("删掉 x.txt"))
    types = [e["type"] for e in events]
    # 一条流到底:confirm 之后继续 tool/result/answer/done,不中断
    assert "confirm" in types and types[-1] == "done"
    assert not (tmp_path / "x.txt").exists()
    assert s.status == "done"


def test_confirm_callback_deny(tmp_path):
    (tmp_path / "x.txt").write_text("x", encoding="utf-8")

    async def deny_all(call: dict) -> bool:
        return False

    s, _ = _session(tmp_path, [
        _tc("delete_path", {"path": "x.txt"}),
        _answer("好,不删"),
    ], on_confirm=deny_all)
    events = _collect(s.run("删掉 x.txt"))
    assert events[-1]["type"] == "done"
    assert (tmp_path / "x.txt").exists()


# ---------- 临时授权 ----------

def test_temporary_grant_from_user_message(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "o.txt").write_text("外面的", encoding="utf-8")

    scope = DirScope(cwd=str(root), allowed_roots=[str(root)])
    reg = ToolRegistry(scope)
    prov = ScriptedProvider([
        _tc("read_file", {"path": str(outside / "o.txt")}),
        _answer("读到了"),
    ])
    s = AgentSession(provider=prov, registry=reg, store=MemoryStore(),
                     checkpoint=False)
    task = f"帮我看看 {outside / 'o.txt'} 写了什么"
    events = _collect(s.run(task))
    # info 事件提示了临时授权;工具真的读到了白名单外的文件
    assert any(e["type"] == "info" and "临时授权" in e.get("content", "")
               for e in events)
    results = [e for e in events if e["type"] == "result"]
    assert "外面的" in str(results[0]["result"])
    # 给模型的 user 消息是「附带授权提示的版本」,transcript 里是原文
    assert prov.calls[0][0][1]["content"].startswith("[本轮临时授权")
    user_events = [e for e in events if e["type"] == "user"]
    assert user_events[0]["content"] == task


# ---------- 反铺垫 nudge ----------

def test_nudge_on_preamble_only_reply(tmp_path):
    s, prov = _session(tmp_path, [
        _answer("我会先看看项目结构,然后开始动手。"),  # 只铺垫没动手
        _answer("真正的最终答复"),
    ])
    events = _collect(s.run("帮我改个东西"))
    assert any(e["type"] == "info" and "续" in e.get("content", "")
               for e in events)
    assert events[-2]["content"] == "真正的最终答复"
    # nudge 注入了一条 user 消息
    assert any("不要只说" in m.get("content", "")
               for m in prov.calls[1][0] if m.get("role") == "user")


def test_nudge_disabled(tmp_path):
    s, _ = _session(tmp_path, [
        _answer("我会先看看项目结构,然后开始动手。"),
    ], nudge=False)
    events = _collect(s.run("帮我改个东西"))
    assert events[-1]["type"] == "done"          # 不续刀,直接当答案收尾


# ---------- 检查点 ----------

def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, timeout=30)


def _init_git(tmp_path):
    _git(["init", "-b", "main"], tmp_path)
    _git(["config", "user.email", "t@t"], tmp_path)
    _git(["config", "user.name", "t"], tmp_path)


def test_checkpoint_lazy_skips_pure_chat(tmp_path):
    """懒打:git 仓库里纯聊天那一轮不打检查点(不再污染历史/留空 commit)。"""
    _init_git(tmp_path)
    s, _ = _session(tmp_path, [_answer("好")], checkpoint=True)
    events = _collect(s.run("随便聊聊"))
    assert events[-1]["type"] == "done"
    assert not any(e["type"] == "checkpoint" for e in events)
    assert s.checkpoints == []


def test_checkpoint_lazy_before_first_mutation(tmp_path):
    """懒打:改文件那一轮,在改动工具执行前打恰好一个检查点。"""
    _init_git(tmp_path)
    s, _ = _session(
        tmp_path,
        [_tc("write_file", {"path": "a.txt", "content": "hi"}),
         _answer("写好了")],
        checkpoint=True,
    )
    events = _collect(s.run("写个 a.txt"))
    types = [e["type"] for e in events]
    assert types.count("checkpoint") == 1
    assert types.index("checkpoint") < types.index("tool")  # 点在改动前
    assert len(s.checkpoints) == 1
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "hi"


def test_checkpoint_one_per_turn(tmp_path):
    """一轮多次改动只打一个点;下一轮再改又打一个(每轮一个)。"""
    _init_git(tmp_path)
    s, _ = _session(
        tmp_path,
        [_tc("write_file", {"path": "a.txt", "content": "1"}, "c1"),
         _tc("write_file", {"path": "b.txt", "content": "2"}, "c2"),
         _answer("两个都写了"),
         _tc("write_file", {"path": "c.txt", "content": "3"}, "c3"),
         _answer("又写了一个")],
        checkpoint=True,
    )
    ev1 = _collect(s.run("写 a 和 b"))
    assert [e["type"] for e in ev1].count("checkpoint") == 1   # 一轮一个
    assert len(s.checkpoints) == 1
    ev2 = _collect(s.continue_("再写 c"))
    assert [e["type"] for e in ev2].count("checkpoint") == 1   # 这一轮也一个
    assert len(s.checkpoints) == 2                             # 累计两个


def test_checkpoint_non_git_warns_but_proceeds(tmp_path):
    """非 git 目录开了检查点:改文件那轮打点失败 → 出 info 警告,但改动
    照常完成,不再像旧版那样整轮 error。"""
    s, _ = _session(
        tmp_path,
        [_tc("write_file", {"path": "a.txt", "content": "hi"}),
         _answer("写好了")],
        checkpoint=True,
    )
    events = _collect(s.run("写 a.txt"))
    assert events[-1]["type"] == "done"
    assert not any(e["type"] == "checkpoint" for e in events)
    assert any(e["type"] == "info" and "检查点失败" in e.get("content", "")
               for e in events)
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "hi"
    assert s.checkpoints == []


def test_checkpoint_off_works_anywhere(tmp_path):
    s, _ = _session(tmp_path, [_answer("好")], checkpoint=False)
    events = _collect(s.run("随便"))
    assert events[-1]["type"] == "done"
    assert not any(e["type"] == "checkpoint" for e in events)


def test_checkpoints_persist_and_reload(tmp_path):
    """checkpoints 列表 + checkpoint_enabled 跨'重启'持久化恢复。"""
    _init_git(tmp_path)
    store = MemoryStore()
    scope = DirScope(cwd=str(tmp_path), allowed_roots=[str(tmp_path)])
    s1 = AgentSession(
        provider=ScriptedProvider(
            [_tc("write_file", {"path": "a.txt", "content": "1"}),
             _answer("done")]),
        registry=ToolRegistry(scope), store=store,
        confirm_policy=RiskyConfirmPolicy("none"), checkpoint=True)
    _collect(s1.run("写 a"))
    assert len(s1.checkpoints) == 1
    saved = store.load(s1.id)
    assert saved["checkpoints"] == s1.checkpoints
    assert saved["checkpoint_enabled"] is True
    # 重启载回:两者都恢复(纯聊天会话没点,但 enabled 仍要续上)
    s2 = AgentSession.load(
        s1.id, provider=ScriptedProvider([_answer("hi")]),
        registry=ToolRegistry(scope), store=store,
        confirm_policy=RiskyConfirmPolicy("none"),
        checkpoint=bool(saved.get("checkpoint_enabled")))
    assert s2 is not None
    assert s2.checkpoints == s1.checkpoints
    assert s2.checkpoint_enabled is True


def test_excluded_tool_is_blocked_from_execution(tmp_path):
    """被 exclude 的工具:模型即便调用也不执行,回一条『不可用』错误。
    (exclude = 禁止执行,不只是不告诉模型 —— 纵深防御)"""
    s, _ = _session(
        tmp_path,
        [_tc("write_file", {"path": "a.txt", "content": "x"}),
         _answer("行")],
        checkpoint=False,
    )
    events = _collect(s.run("写 a", exclude_tools={"write_file"}))
    assert not (tmp_path / "a.txt").exists()        # 没真执行
    assert any(e["type"] == "result"
               and isinstance(e.get("result"), dict)
               and "不可用" in str(e["result"].get("error", ""))
               for e in events)
    assert events[-1]["type"] == "done"


def test_no_double_checkpoint_across_awaiting_restart(tmp_path):
    """一轮内先打了点 A → 第二个工具需确认 → awaiting 落盘 → '重启'载回 →
    批准续跑:不能再打第二个点(_turn_checkpointed 已持久化恢复)。"""
    _init_git(tmp_path)
    store = MemoryStore()
    scope = DirScope(cwd=str(tmp_path), allowed_roots=[str(tmp_path)])
    s1 = AgentSession(
        provider=ScriptedProvider(
            [_tc("write_file", {"path": "a.txt", "content": "1"}, "c1"),
             _tc("run_command", {"command": "echo hi"}, "c2")]),
        registry=ToolRegistry(scope), store=store,
        confirm_policy=RiskyConfirmPolicy("risky"), checkpoint=True)
    ev1 = _collect(s1.run("写 a 再跑命令"))
    assert ev1[-1]["type"] == "confirm" and s1.status == "awaiting"
    assert len(s1.checkpoints) == 1                 # 只打了 A
    # "重启":全新对象从 store 载回(_turn_checkpointed 应一并恢复)
    s2 = AgentSession.load(
        s1.id, provider=ScriptedProvider([_answer("done")]),
        registry=ToolRegistry(scope), store=store,
        confirm_policy=RiskyConfirmPolicy("risky"), checkpoint=True)
    assert s2 is not None and s2._turn_checkpointed is True
    ev2 = _collect(s2.respond(approve=True))
    assert ev2[-1]["type"] == "done"
    assert len(s2.checkpoints) == 1                 # 仍只有 A,没多打 B


def test_context_hook_appends_to_tool_result(tmp_path):
    """通用 context_hook:工具执行后回调,返回的文本拼到该工具结果末尾给模型。"""
    scope = DirScope(cwd=str(tmp_path), allowed_roots=[str(tmp_path)])
    (tmp_path / "a.txt").write_text("hi", encoding="utf-8")
    seen: list[str] = []

    def hook(name, args, result):
        seen.append(name)
        return "[注入:本目录指令]" if name == "read_file" else None

    prov = ScriptedProvider([
        _tc("read_file", {"path": "a.txt"}),
        _answer("读完了"),
    ])
    s = AgentSession(provider=prov, registry=ToolRegistry(scope),
                     store=MemoryStore(),
                     confirm_policy=RiskyConfirmPolicy("none"),
                     checkpoint=False, context_hook=hook)
    _collect(s.run("读 a.txt"))
    assert "read_file" in seen
    tool_msgs = [m for m in s.messages if m.get("role") == "tool"]
    assert any("[注入:本目录指令]" in m["content"] for m in tool_msgs)


# ---------- 取消 ----------

def test_cancel_before_tool_execution(tmp_path):
    (tmp_path / "x.txt").write_text("x", encoding="utf-8")
    s, prov = _session(tmp_path, [
        _tc("read_file", {"path": "x.txt"}),
        _answer("不会到这"),
    ])

    orig = prov.tool_complete

    async def cancelling(messages, tools):
        r = await orig(messages, tools)
        s.cancel()                                # 模型一返回就有人点了停止
        return r

    prov.tool_complete = cancelling
    events = _collect(s.run("读文件"))
    assert events[-1]["type"] == "cancelled"
    assert s.status == "cancelled"
    assert not any(e["type"] == "result" for e in events)  # 工具没执行


# ---------- 持久化 / 跨重启恢复 ----------

def test_persist_and_resume_after_restart(tmp_path):
    store = MemoryStore()
    scope = DirScope(cwd=str(tmp_path), allowed_roots=[str(tmp_path)])
    s1 = AgentSession(
        provider=ScriptedProvider([_answer("第一轮答复")]),
        registry=ToolRegistry(scope), store=store, checkpoint=False)
    _collect(s1.run("第一轮"))
    sid = s1.id

    # "重启":全新对象,从 store 载回,继续追加一轮
    s2 = AgentSession.load(
        sid, provider=ScriptedProvider([_answer("第二轮答复")]),
        registry=ToolRegistry(scope), store=store, checkpoint=False)
    assert s2 is not None and s2.status == "done"
    events = _collect(s2.continue_("第二轮"))
    assert events[-1]["type"] == "done"
    users = [m for m in s2.messages if m["role"] == "user"]
    assert len(users) == 2                        # 两轮都在上下文里


def test_awaiting_survives_restart(tmp_path):
    """旧版重启后 batch 丢失,确认中的会话变砖;现在 batch/bi 也落盘。"""
    (tmp_path / "x.txt").write_text("x", encoding="utf-8")
    store = MemoryStore()
    scope = DirScope(cwd=str(tmp_path), allowed_roots=[str(tmp_path)])
    s1 = AgentSession(
        provider=ScriptedProvider([_tc("delete_path", {"path": "x.txt"})]),
        registry=ToolRegistry(scope), store=store, checkpoint=False)
    ev = _collect(s1.run("删 x.txt"))
    assert ev[-1]["type"] == "confirm" and s1.status == "awaiting"

    s2 = AgentSession.load(
        s1.id, provider=ScriptedProvider([_answer("删除完成")]),
        registry=ToolRegistry(scope), store=store, checkpoint=False)
    ev2 = _collect(s2.respond(approve=True))      # 重启后仍能批准续跑
    assert ev2[-1]["type"] == "done"
    assert not (tmp_path / "x.txt").exists()


def test_respond_without_pending_yields_error(tmp_path):
    s, _ = _session(tmp_path, [_answer("好")])
    _collect(s.run("随便"))
    ev = _collect(s.respond(approve=True))
    assert ev and ev[0]["type"] == "error"


# ---------- 工具排除(web 开关这类) ----------

def test_exclude_tools_filters_specs(tmp_path):
    s, prov = _session(tmp_path, [_answer("好")])
    _collect(s.run("随便", exclude_tools={"run_command"}))
    tools = prov.calls[0][1]
    names = [t["function"]["name"] for t in tools]
    assert "run_command" not in names and "read_file" in names


# ---------- run_from:宿主管历史(无状态宿主,如文件助手) ----------

def test_run_from_uses_host_history(tmp_path):
    (tmp_path / "a.txt").write_text("数据A", encoding="utf-8")
    s, prov = _session(tmp_path, [
        _tc("read_file", {"path": "a.txt"}),
        _answer("读完了"),
    ])
    history = [
        {"role": "user", "content": "之前聊过的"},
        {"role": "assistant", "content": "嗯"},
        {"role": "user", "content": "读一下 a.txt"},
    ]
    events = _collect(s.run_from(history))
    assert events[-1]["type"] == "done"
    # 模型收到 [system] + 宿主全量历史,不额外追加 user
    first_msgs = prov.calls[0][0]
    assert first_msgs[0]["role"] == "system"
    assert [m["content"] for m in first_msgs[1:4]] == [
        "之前聊过的", "嗯", "读一下 a.txt"]
    results = [e for e in events if e["type"] == "result"]
    assert "数据A" in str(results[0]["result"])


def test_run_from_confirm_then_respond(tmp_path):
    (tmp_path / "x.txt").write_text("x", encoding="utf-8")
    s, _ = _session(tmp_path, [
        _tc("delete_path", {"path": "x.txt"}),
        _answer("删除完成"),
    ])
    ev1 = _collect(s.run_from([{"role": "user", "content": "删 x.txt"}]))
    assert ev1[-1]["type"] == "confirm" and s.status == "awaiting"
    ev2 = _collect(s.respond(approve=True))
    assert ev2[-1]["type"] == "done"
    assert not (tmp_path / "x.txt").exists()


# ---------- 流式 provider:循环逐 token 透出 ----------

class ScriptedStreamingProvider:
    """有 stream_tool_complete 的脚本化 provider:每轮按预排事件吐。"""

    def __init__(self, turns: list[list[tuple]]) -> None:
        self.turns = list(turns)

    async def stream_tool_complete(self, messages, tools):
        for ev in self.turns.pop(0):
            yield ev

    async def tool_complete(self, messages, tools):  # 不应被调用
        raise AssertionError("有流式能力时循环应走 stream_tool_complete")


def test_session_streams_deltas_with_streaming_provider(tmp_path):
    (tmp_path / "a.txt").write_text("hi", encoding="utf-8")
    scope = DirScope(cwd=str(tmp_path), allowed_roots=[str(tmp_path)])
    prov = ScriptedStreamingProvider([
        [("final", _tc("read_file", {"path": "a.txt"}))],     # 第一轮:调工具
        [("reasoning", "想想"), ("answer", "读"), ("answer", "完了"),
         ("final", _answer("读完了"))],                        # 第二轮:流式作答
    ])
    s = AgentSession(provider=prov, registry=ToolRegistry(scope),
                     store=MemoryStore(), checkpoint=False)
    events = _collect(s.run("读 a.txt"))
    # 增量事件逐段透出
    assert [e["content"] for e in events if e["type"] == "delta"] == \
        ["读", "完了"]
    assert any(e["type"] == "reasoning" for e in events)
    # 最终 answer/done 仍在(老消费者不受影响)
    assert events[-2] == {"type": "answer", "content": "读完了"}
    assert events[-1]["type"] == "done"
    # delta/reasoning 是瞬时事件,不进 transcript(不膨胀持久化)
    assert not any(e["type"] in ("delta", "reasoning") for e in s.transcript)
    assert any(e["type"] == "answer" for e in s.transcript)


# ---------- provider 出错 ----------

def test_provider_exception_becomes_error_event(tmp_path):
    class Boom:
        async def tool_complete(self, messages, tools):
            raise RuntimeError("上游炸了")

    scope = DirScope(cwd=str(tmp_path), allowed_roots=[str(tmp_path)])
    s = AgentSession(provider=Boom(), registry=ToolRegistry(scope),
                     store=MemoryStore(), checkpoint=False)
    events = _collect(s.run("随便"))
    assert events[-1]["type"] == "error" and s.status == "error"
