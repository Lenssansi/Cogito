"""普通对话的全盘文件工具（独立于 P4 编程 Agent 的 agent_tools）。

安全档位（用户已拍板）：
- 读/列/搜：任意路径，不弹窗（信息层面）
- 增/写/改/删：任意路径，但每次必须用户确认（防误删）
- 不含 shell / git
- 远程访问禁用（在端点层 loopback 才放行）
相对路径按"会话绑定目录"解析；绝对路径可达当前用户能访问的全盘。
"""

from __future__ import annotations

import contextvars
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

MAX_READ = 200_000
CMD_TIMEOUT = 120


class FsError(Exception):
    pass


# ---- 可中断:跟踪每个 run 当前正在跑的子进程,允许外部 stop ----
# 通过 ContextVar 把当前 run_id 隐式传给 run_command(不必改 run_tool 签名)。
# chat_agent._drive 调用前 set,调用后 reset。
_CURRENT_RID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "chatfs_rid", default=None,
)
_PROCS: dict[str, subprocess.Popen] = {}
_PROCS_LOCK = threading.Lock()


def kill_run(rid: str) -> bool:
    """外部(/api/chatfs/stop)调:终结某 run 当前正在跑的子进程。
    返回是否找到并终结。"""
    if not rid:
        return False
    with _PROCS_LOCK:
        p = _PROCS.pop(rid, None)
    if not p:
        return False
    try:
        p.kill()
    except OSError:
        pass
    return True


def _decode(b: bytes) -> str:
    """中文 Windows 上很多文件是 GBK/GB18030，先 utf-8 再回退，消除乱码。"""
    for enc in ("utf-8", "gb18030", "utf-16"):
        try:
            return b.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return b.decode("latin-1", "replace")


def _p(path: str, base: str) -> Path:
    if not path:
        raise FsError("路径为空")
    p = Path(path)
    if not p.is_absolute():
        p = Path(base or os.getcwd()) / p
    return p.resolve()


def user_dirs(base: str = "") -> dict[str, Any]:  # noqa: ARG001
    """返回本机真实 用户名/主目录/桌面/下载/文档 绝对路径（只读）。"""
    import userdirs
    return userdirs.user_dirs()


def list_dir(path: str = ".", base: str = "") -> dict[str, Any]:
    d = _p(path, base)
    if not d.exists():
        raise FsError(f"不存在：{d}")
    if not d.is_dir():
        raise FsError(f"不是目录：{d}")
    items = [("dir " if e.is_dir() else "file") + e.name
             for e in sorted(d.iterdir())]
    return {"path": str(d), "entries": items[:800]}


def read_file(path: str, base: str = "") -> dict[str, Any]:
    f = _p(path, base)
    if not f.is_file():
        raise FsError(f"不是文件或不存在：{f}")
    t = _decode(f.read_bytes())
    return {"path": str(f), "content": t[:MAX_READ],
            "truncated": len(t) > MAX_READ}


def search_text(query: str, path: str = ".", exts: str = "",
                base: str = "") -> dict[str, Any]:
    root = _p(path, base)
    es = {e.strip().lstrip(".") for e in exts.split(",") if e.strip()}
    hits: list[str] = []
    for r, _d, fs in os.walk(root):
        if any(s in r for s in (".git", "node_modules", ".venv",
                                "__pycache__")):
            continue
        for fn in fs:
            if es and Path(fn).suffix.lstrip(".") not in es:
                continue
            fp = Path(r) / fn
            try:
                for i, ln in enumerate(
                    _decode(fp.read_bytes()).splitlines(), 1
                ):
                    if query in ln:
                        hits.append(f"{fp}:{i}: {ln.strip()[:200]}")
                        if len(hits) >= 200:
                            return {"matches": hits, "truncated": True}
            except OSError:
                continue
    return {"matches": hits, "truncated": False}


def create_file(path: str, content: str = "", base: str = "") -> dict:
    f = _p(path, base)
    if f.exists():
        raise FsError(f"已存在，拒绝覆盖（用 write_file）：{f}")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(content, encoding="utf-8")
    return {"path": str(f), "created": True}


def write_file(path: str, content: str, base: str = "") -> dict:
    f = _p(path, base)
    existed = f.exists()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(content, encoding="utf-8")
    return {"path": str(f), "overwritten": existed}


def edit_file(path: str, old: str, new: str, base: str = "") -> dict:
    f = _p(path, base)
    if not f.is_file():
        raise FsError(f"不是文件：{f}")
    t = _decode(f.read_bytes())
    n = t.count(old)
    if n == 0:
        raise FsError("未找到要替换的 old 文本（需逐字精确匹配）")
    if n > 1:
        raise FsError(f"old 出现 {n} 次不唯一，请给更长上下文")
    f.write_text(t.replace(old, new, 1), encoding="utf-8")
    return {"path": str(f), "replaced": True}


def run_command(command: str, cwd: str = "",
                base: str = "") -> dict[str, Any]:
    """跑命令行(bat/exe/python 等),供普通对话也能让 AI 执行脚本。
    高危——执行前会弹用户确认。无白名单约束(普通对话本就全盘可访问)。

    可中断:用 Popen+communicate,把进程登记到 _PROCS;外部调 kill_run(rid)
    能立刻 terminate,communicate 立刻返回。"""
    if not command or not command.strip():
        raise FsError("命令为空")
    work_dir = (cwd or base or os.getcwd())
    try:
        if not Path(work_dir).is_dir():
            work_dir = os.getcwd()
    except OSError:
        work_dir = os.getcwd()

    rid = _CURRENT_RID.get()
    try:
        proc = subprocess.Popen(
            command, shell=True, cwd=work_dir,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, errors="replace",
            creationflags=(0x08000000 if os.name == "nt" else 0),
        )
    except OSError as e:
        raise FsError(f"启动失败:{e}") from None
    if rid:
        with _PROCS_LOCK:
            _PROCS[rid] = proc
    try:
        try:
            out, err = proc.communicate(timeout=CMD_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                out, err = proc.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                out, err = "", ""
            raise FsError(f"命令超时(>{CMD_TIMEOUT}s),已强制终止") from None
    finally:
        if rid:
            with _PROCS_LOCK:
                _PROCS.pop(rid, None)
    # proc 被外部 kill_run 终止时 returncode 会是 -1/-9 之类
    killed_externally = proc.returncode is not None and proc.returncode < 0
    return {
        "exit_code": proc.returncode,
        "stdout": (out or "")[-8000:],
        "stderr": (err or "")[-4000:],
        "cwd": work_dir,
        "killed": killed_externally,
    }


def delete_path(path: str, base: str = "") -> dict:
    p = _p(path, base)
    if not p.exists():
        raise FsError(f"不存在：{p}")
    if p.is_dir():
        shutil.rmtree(p)
    else:
        p.unlink()
    return {"path": str(p), "deleted": True}


# name -> (handler, 是否高危需确认)
REGISTRY = {
    "user_dirs": (user_dirs, False),
    "list_dir": (list_dir, False),
    "read_file": (read_file, False),
    "search_text": (search_text, False),
    "create_file": (create_file, True),
    "write_file": (write_file, True),
    "edit_file": (edit_file, True),
    "delete_path": (delete_path, True),
    "run_command": (run_command, True),
}


def is_high_risk(name: str) -> bool:
    return REGISTRY.get(name, (None, True))[1]


def run_tool(name: str, args: dict[str, Any], base: str) -> dict[str, Any]:
    if name not in REGISTRY:
        return {"error": f"未知工具：{name}"}
    try:
        return REGISTRY[name][0](**{**(args or {}), "base": base})
    except FsError as e:
        return {"error": str(e)}
    except TypeError as e:
        return {"error": f"参数错误：{e}"}
    except Exception as e:  # noqa: BLE001
        return {"error": f"执行失败：{e}"}


def tool_specs() -> list[dict[str, Any]]:
    S = "string"

    def fn(name, desc, props, req):
        return {"type": "function", "function": {
            "name": name, "description": desc,
            "parameters": {"type": "object", "properties": props,
                           "required": req}}}

    return [
        fn("user_dirs",
           "取本机真实 用户名/主目录/桌面/下载/文档 绝对路径"
           "(找桌面等位置先调它,别猜用户名)", {}, []),
        fn("list_dir", "列目录(相对路径按会话目录,绝对路径全盘)",
           {"path": {"type": S}}, []),
        fn("read_file", "读文件", {"path": {"type": S}}, ["path"]),
        fn("search_text", "在目录内搜文本",
           {"query": {"type": S}, "path": {"type": S},
            "exts": {"type": S}}, ["query"]),
        fn("create_file", "新建文件(不存在才行)",
           {"path": {"type": S}, "content": {"type": S}}, ["path"]),
        fn("write_file", "写/覆盖文件",
           {"path": {"type": S}, "content": {"type": S}},
           ["path", "content"]),
        fn("edit_file", "精确替换文件中一段文本(old须唯一)",
           {"path": {"type": S}, "old": {"type": S}, "new": {"type": S}},
           ["path", "old", "new"]),
        fn("delete_path", "删除文件或目录",
           {"path": {"type": S}}, ["path"]),
        fn("run_command",
           "在指定目录执行 shell 命令(可跑 bat/exe/python 等任意可执行文件;"
           "高危,会先弹用户确认)",
           {"command": {"type": S},
            "cwd": {"type": S, "description": "工作目录(可空,默认会话目录)"}},
           ["command"]),
    ]
