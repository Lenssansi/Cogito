"""普通对话的「文件」模式工具循环（独立于 P4 编程 Agent）。

读/列/搜自动执行；增/写/改/删高危——执行前暂停要用户确认。
无 git 检查点（确认本身就是这里的安全网，用户已拍板）。
会话历史由前端每轮带上；本模块只维护"本轮工具循环"的服务端状态，
高危确认通过 /api/chatfs/respond 续跑。远程禁用在端点层强制。
"""

from __future__ import annotations

import json
import uuid
from typing import Any, AsyncIterator

from config import confirm_required, get_active_resolved
from providers.openai_compat import OpenAICompatProvider
import chatfs
import settings_tools
import userdirs

RUNS: dict[str, dict[str, Any]] = {}

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
_TOOLSETS = {"file": chatfs, "settings": settings_tools}


def _provider() -> tuple[OpenAICompatProvider | None, str]:
    """选支持工具调用的 provider;思考模式下兜底到本地。"""
    from config import resolve_tool_capable
    r, err = resolve_tool_capable()
    if err:
        return None, err
    if not r or not r.get("api_key"):
        return None, "未配置可用 API key"
    return OpenAICompatProvider(r), ""


def _sse(o: dict) -> bytes:
    return f"data: {json.dumps(o, ensure_ascii=False)}\n\n".encode("utf-8")


def start(messages: list[dict], base: str,
          mode: str = "file") -> tuple[str | None, str | None]:
    prov, perr = _provider()
    if prov is None:
        return None, perr or "未配置可用 API"
    ts = _TOOLSETS.get(mode, chatfs)
    sysc = (_SYS_SET if mode == "settings"
            else _SYS_FILE.format(base=base or "(默认)",
                                  dirs=userdirs.prompt_hint()))
    rid = uuid.uuid4().hex[:12]
    RUNS[rid] = {
        "messages": [{"role": "system", "content": sysc}] + list(messages),
        "base": base or "",
        "ts": ts,
        "batch": [], "bi": 0, "status": "running",
    }
    return rid, None


async def _drive(rid: str) -> AsyncIterator[bytes]:
    run = RUNS[rid]
    ts = run["ts"]
    prov, perr = _provider()
    if prov is None:
        yield _sse({"type": "error", "error": perr or "云端 API 不可用"})
        return
    # 把当前 rid 放进 chatfs 的 ContextVar,run_command 据此把子进程登记
    # 到 _PROCS。外部 /api/chatfs/stop 能据此 kill 它。
    rid_token = chatfs._CURRENT_RID.set(rid)
    try:
        while True:
            if run.get("cancelled"):
                yield _sse({"type": "cancelled"})
                RUNS.pop(rid, None)
                return
            while run["bi"] < len(run["batch"]):
                if run.get("cancelled"):
                    yield _sse({"type": "cancelled"})
                    RUNS.pop(rid, None)
                    return
                c = run["batch"][run["bi"]]
                if (confirm_required(c["name"], ts.is_high_risk(c["name"]))
                        and not c.get("_decided")):
                    run["status"] = "awaiting"
                    yield _sse({"type": "confirm", "tool": c["name"],
                                "args": c["arguments"], "call_id": c["id"]})
                    return
                if c.get("_denied"):
                    content = "用户拒绝了该操作，请改用别的方式或询问用户。"
                else:
                    yield _sse({"type": "tool", "name": c["name"],
                                "args": c["arguments"]})
                    res = ts.run_tool(c["name"], c["arguments"], run["base"])
                    content = json.dumps(res, ensure_ascii=False)[:8000]
                    yield _sse({"type": "result", "name": c["name"],
                                "result": res})
                run["messages"].append({"role": "tool",
                                        "tool_call_id": c["id"],
                                        "content": content})
                run["bi"] += 1
            if run.get("cancelled"):
                yield _sse({"type": "cancelled"})
                RUNS.pop(rid, None)
                return
            try:
                resp = await prov.tool_complete(run["messages"],
                                                run["ts"].tool_specs())
            except Exception as e:  # noqa: BLE001
                yield _sse({"type": "error", "error": f"模型调用失败：{e}"})
                return
            asst: dict[str, Any] = {"role": "assistant",
                                    "content": resp["content"]}
            if resp["tool_calls"]:
                asst["tool_calls"] = [
                    {"id": t["id"], "type": "function",
                     "function": {"name": t["name"],
                                  "arguments": json.dumps(t["arguments"],
                                                          ensure_ascii=False)}}
                    for t in resp["tool_calls"]
                ]
            run["messages"].append(asst)
            if not resp["tool_calls"]:
                run["status"] = "done"
                if resp["content"]:
                    yield _sse({"type": "answer", "content": resp["content"]})
                yield _sse({"type": "done"})
                RUNS.pop(rid, None)
                return
            run["batch"] = resp["tool_calls"]
            run["bi"] = 0
    finally:
        chatfs._CURRENT_RID.reset(rid_token)


def stop_run(rid: str) -> bool:
    """外部调:把指定 run 标 cancelled + 杀掉任何正在跑的子进程。
    _drive 下一个 yield 点会检测到 cancelled 并 yield {type:cancelled} 退出。
    若 run 不存在(可能已结束)则只尝试 kill 还在跑的子进程。"""
    killed = chatfs.kill_run(rid)
    run = RUNS.get(rid)
    if run is not None:
        run["cancelled"] = True
        return True
    return killed


async def stream_start(
    messages: list[dict], base: str, mode: str = "file"
) -> AsyncIterator[bytes]:
    rid, err = start(messages, base, mode)
    if err:
        yield _sse({"type": "error", "error": err})
        return
    yield _sse({"type": "run", "run_id": rid})
    async for ev in _drive(rid):
        yield ev


async def stream_respond(
    rid: str, approve: bool, edited_args: dict | None
) -> AsyncIterator[bytes]:
    run = RUNS.get(rid)
    if not run:
        yield _sse({"type": "error", "error": "会话已过期，请重发消息"})
        return
    if run["status"] != "awaiting" or run["bi"] >= len(run["batch"]):
        yield _sse({"type": "error", "error": "当前无待确认操作"})
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
