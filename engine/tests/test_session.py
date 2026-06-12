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


def test_checkpoint_on_in_git_repo(tmp_path):
    _git(["init", "-b", "main"], tmp_path)
    _git(["config", "user.email", "t@t"], tmp_path)
    _git(["config", "user.name", "t"], tmp_path)
    s, _ = _session(tmp_path, [_answer("好")], checkpoint=True)
    events = _collect(s.run("随便"))
    cp = [e for e in events if e["type"] == "checkpoint"]
    assert cp and cp[0]["commit"]


def test_checkpoint_on_in_non_git_dir_errors(tmp_path):
    s, _ = _session(tmp_path, [_answer("好")], checkpoint=True)
    events = _collect(s.run("随便"))
    assert events[-1]["type"] == "error"
    assert s.status == "error"


def test_checkpoint_off_works_anywhere(tmp_path):
    s, _ = _session(tmp_path, [_answer("好")], checkpoint=False)
    events = _collect(s.run("随便"))
    assert events[-1]["type"] == "done"
    assert not any(e["type"] == "checkpoint" for e in events)


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
