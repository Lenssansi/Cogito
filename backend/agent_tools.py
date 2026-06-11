"""Agent 工具原语 + 作用域护栏。

铁律：任何涉及路径的工具，路径解析后必须落在 settings 的 allowed_roots 内，
否则直接拒绝执行（防目录穿越/越界）。只读工具自动执行；改动/命令类是高危，
由上层会话在执行前向用户确认。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from config import get_workspace, path_in_scope

MAX_READ = 200_000  # 单文件读取上限，防爆上下文
CMD_TIMEOUT = 120


class ToolError(Exception):
    pass


def _decode(b: bytes) -> str:
    """utf-8 失败回退 GB18030/UTF-16，消除中文文件乱码。"""
    for enc in ("utf-8", "gb18030", "utf-16"):
        try:
            return b.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return b.decode("latin-1", "replace")


# 本轮临时授权访问的绝对路径(用户在消息中显式写出的)。由 agent_session
# 每轮从用户消息里抽出后 set_extra_paths,本轮结束后保留到下一轮覆写。
_EXTRA_PATHS: list[str] = []


def set_extra_paths(paths: list[str]) -> None:
    global _EXTRA_PATHS
    cleaned = []
    for p in paths or []:
        try:
            cleaned.append(str(Path(p).resolve()))
        except (OSError, RuntimeError):
            continue
    _EXTRA_PATHS = cleaned


def get_extra_paths() -> list[str]:
    return list(_EXTRA_PATHS)


def _path_under_extras(p: str) -> bool:
    try:
        pr = Path(p).resolve()
    except (OSError, RuntimeError):
        return False
    for r in _EXTRA_PATHS:
        try:
            rp = Path(r)
            if pr == rp or pr.is_relative_to(rp):
                return True
        except (OSError, RuntimeError, ValueError):
            continue
    return False


def _safe(path: str) -> Path:
    if not path:
        raise ToolError("路径为空")
    if path_in_scope(path) or _path_under_extras(path):
        return Path(path).resolve()
    raise ToolError(
        f"拒绝:路径越出授权白名单 → {path}(去设置页加根目录;或在你的"
        "请求中明确写出该绝对路径,Agent 会本轮临时授权访问该路径)"
    )


def _cwd() -> str:
    return get_workspace().get("cwd", "") or os.getcwd()


# ---------- 只读 ----------

def user_dirs() -> dict[str, Any]:
    """返回本机真实 用户名/主目录/桌面/下载/文档 绝对路径（只读）。"""
    import userdirs
    return userdirs.user_dirs()


def list_dir(path: str = ".") -> dict[str, Any]:
    p = _safe(path if os.path.isabs(path) else os.path.join(_cwd(), path))
    if not p.exists():
        raise ToolError(f"不存在：{p}")
    items = []
    for e in sorted(p.iterdir()):
        items.append(("dir " if e.is_dir() else "file") + e.name)
    return {"path": str(p), "entries": items[:500]}


def read_file(path: str) -> dict[str, Any]:
    p = _safe(path if os.path.isabs(path) else os.path.join(_cwd(), path))
    if not p.is_file():
        raise ToolError(f"不是文件或不存在：{p}")
    data = _decode(p.read_bytes())
    truncated = len(data) > MAX_READ
    return {"path": str(p), "content": data[:MAX_READ],
            "truncated": truncated}


def search_text(query: str, path: str = ".",
                exts: str = "") -> dict[str, Any]:
    base = _safe(path if os.path.isabs(path) else os.path.join(_cwd(), path))
    ext_set = {e.strip() for e in exts.split(",") if e.strip()}
    hits = []
    for root, _dirs, files in os.walk(base):
        if any(s in root for s in (".git", "node_modules", ".venv")):
            continue
        for f in files:
            if ext_set and Path(f).suffix.lstrip(".") not in ext_set:
                continue
            fp = Path(root) / f
            try:
                for i, line in enumerate(
                    _decode(fp.read_bytes()).splitlines(), 1
                ):
                    if query in line:
                        hits.append(f"{fp}:{i}: {line.strip()[:200]}")
                        if len(hits) >= 200:
                            return {"matches": hits, "truncated": True}
            except OSError:
                continue
    return {"matches": hits, "truncated": False}


# ---------- 改动（高危，需确认）----------

def create_file(path: str, content: str = "") -> dict[str, Any]:
    p = _safe(path if os.path.isabs(path) else os.path.join(_cwd(), path))
    if p.exists():
        raise ToolError(f"已存在，拒绝覆盖（用 write_file）：{p}")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return {"path": str(p), "created": True}


def write_file(path: str, content: str) -> dict[str, Any]:
    p = _safe(path if os.path.isabs(path) else os.path.join(_cwd(), path))
    existed = p.exists()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return {"path": str(p), "overwritten": existed}


def edit_file(path: str, old: str, new: str) -> dict[str, Any]:
    p = _safe(path if os.path.isabs(path) else os.path.join(_cwd(), path))
    if not p.is_file():
        raise ToolError(f"不是文件：{p}")
    text = _decode(p.read_bytes())
    n = text.count(old)
    if n == 0:
        raise ToolError("未找到要替换的 old 文本（需逐字精确匹配）")
    if n > 1:
        raise ToolError(f"old 文本出现 {n} 次不唯一，请给更长的上下文")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")
    return {"path": str(p), "replaced": True}


def delete_path(path: str) -> dict[str, Any]:
    p = _safe(path if os.path.isabs(path) else os.path.join(_cwd(), path))
    if not p.exists():
        raise ToolError(f"不存在：{p}")
    if p.is_dir():
        shutil.rmtree(p)
    else:
        p.unlink()
    return {"path": str(p), "deleted": True}


def run_command(command: str) -> dict[str, Any]:
    cwd = _cwd()
    if not path_in_scope(cwd):
        raise ToolError("当前工作目录不在授权白名单内")
    try:
        r = subprocess.run(
            command, shell=True, cwd=cwd, capture_output=True,
            text=True, timeout=CMD_TIMEOUT, errors="replace",
        )
    except subprocess.TimeoutExpired:
        raise ToolError(f"命令超时（>{CMD_TIMEOUT}s）")
    out = (r.stdout or "")[-8000:]
    err = (r.stderr or "")[-4000:]
    return {"exit_code": r.returncode, "stdout": out, "stderr": err}


# ---------- Git 安全网 ----------

def _git(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=_cwd(), capture_output=True, text=True,
        timeout=60, errors="replace",
    )


def git_checkpoint(message: str = "ai-helper checkpoint") -> dict[str, Any]:
    cwd = _cwd()
    if not path_in_scope(cwd):
        raise ToolError("工作目录不在授权白名单内")
    if not (Path(cwd) / ".git").exists():
        raise ToolError("当前工作目录不是 git 仓库（可先 git init）")
    _git(["add", "-A"])
    head_before = _git(["rev-parse", "HEAD"]).stdout.strip()
    c = _git(["commit", "-m", message, "--allow-empty"])
    head = _git(["rev-parse", "HEAD"]).stdout.strip()
    return {"checkpoint": head, "prev": head_before,
            "info": c.stdout.strip() or c.stderr.strip()}


def git_rollback(to: str) -> dict[str, Any]:
    if not to:
        raise ToolError("缺少回滚目标 commit")
    r = _git(["reset", "--hard", to])
    if r.returncode != 0:
        raise ToolError(f"回滚失败：{r.stderr.strip()}")
    return {"rolled_back_to": to, "info": r.stdout.strip()}


def git_init(path: str) -> dict[str, Any]:
    p = _safe(path)
    if not p.is_dir():
        raise ToolError(f"不是目录：{p}")
    if (p / ".git").exists():
        return {"path": str(p), "already_git": True}
    r = subprocess.run(["git", "init"], cwd=str(p), capture_output=True,
                        text=True, timeout=30)
    if r.returncode != 0:
        raise ToolError(f"git init 失败：{r.stderr.strip()}")
    return {"path": str(p), "initialized": True}


# ---------- TODO 清单(给前端 Claude 风格的待办面板用)----------

_TODOS: list[dict[str, Any]] = []


def set_todos(items: list[dict[str, Any]]) -> None:
    """agent_session 在每轮起始处把上一轮 RUNS[rid]['todos'] 推回模块全局,
    让本轮的 todo_update 能在正确的列表上操作。"""
    global _TODOS
    _TODOS = list(items or [])


def get_todos() -> list[dict[str, Any]]:
    return list(_TODOS)


def _norm_status(s: str | None) -> str:
    v = (s or "pending").strip().lower()
    if v in ("done", "complete", "completed", "✓"):
        return "completed"
    if v in ("doing", "active", "running", "in_progress", "wip"):
        return "in_progress"
    return "pending"


def todo_set(items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """覆盖式重设整个 TODO 列表。
    items 每项可含 {id?, title, status?}; status: pending/in_progress/completed。
    返回 {todos:[...]}, 由前端在右侧 TODO 面板渲染环形进度条。"""
    global _TODOS
    out: list[dict[str, Any]] = []
    for i, it in enumerate(items or []):
        if not isinstance(it, dict):
            continue
        title = str(it.get("title") or "").strip()
        if not title:
            continue
        tid = str(it.get("id") or f"t{i + 1}")
        out.append({
            "id": tid,
            "title": title[:200],
            "status": _norm_status(it.get("status")),
        })
    _TODOS = out
    return {"todos": out, "count": len(out)}


def todo_update(id: str, status: str) -> dict[str, Any]:
    """把某条 todo 状态改成 pending/in_progress/completed。"""
    global _TODOS
    target = _norm_status(status)
    found = False
    for t in _TODOS:
        if t["id"] == id:
            t["status"] = target
            found = True
            break
    if not found:
        return {"error": f"未找到 todo id={id}",
                "todos": list(_TODOS)}
    return {"todos": list(_TODOS)}


def web_search(query: str, n: int = 5) -> dict[str, Any]:
    """联网搜索（无需 key，只读，自动执行）。供查使用文档/实时信息。"""
    from search import web_search_sync
    try:
        k = int(n)
    except (TypeError, ValueError):
        k = 5
    results = web_search_sync(query, max(1, min(k, 8)))
    return {"query": query, "results": results, "count": len(results)}


def run_tests() -> dict[str, Any]:
    cmd = get_workspace().get("test_cmd", "").strip()
    if not cmd:
        return {"skipped": True, "reason": "未配置测试命令（设置页可配）"}
    return {"test_cmd": cmd, **run_command(cmd)}


# name -> (handler, high_risk 需用户确认)
REGISTRY: dict[str, tuple[Any, bool]] = {
    "user_dirs": (user_dirs, False),
    "todo_set": (todo_set, False),
    "todo_update": (todo_update, False),
    "list_dir": (list_dir, False),
    "read_file": (read_file, False),
    "search_text": (search_text, False),
    "web_search": (web_search, False),
    "create_file": (create_file, True),
    "write_file": (write_file, True),
    "edit_file": (edit_file, True),
    "delete_path": (delete_path, True),
    "run_command": (run_command, True),
    "git_checkpoint": (git_checkpoint, False),
    "git_rollback": (git_rollback, True),
    "run_tests": (run_tests, False),
}


def is_high_risk(name: str) -> bool:
    return REGISTRY.get(name, (None, True))[1]


def run_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name not in REGISTRY:
        return {"error": f"未知工具：{name}"}
    fn = REGISTRY[name][0]
    try:
        return fn(**(args or {}))
    except ToolError as e:
        return {"error": str(e)}
    except TypeError as e:
        return {"error": f"参数错误：{e}"}
    except Exception as e:  # noqa: BLE001
        return {"error": f"执行失败：{e}"}


# 给模型的工具 schema（OpenAI function-calling 格式）
def tool_specs() -> list[dict[str, Any]]:
    S = "string"

    def fn(name, desc, props, req):
        return {
            "type": "function",
            "function": {
                "name": name, "description": desc,
                "parameters": {
                    "type": "object", "properties": props,
                    "required": req,
                },
            },
        }

    return [
        fn("user_dirs",
           "取本机真实 用户名/主目录/桌面/下载/文档 绝对路径"
           "(涉及用户目录先调它,别猜 admin)", {}, []),
        fn("todo_set",
           "(非简单任务用)开局列出 3-8 项待办,覆盖式重设。"
           "每项:{id,title,status: pending|in_progress|completed}。"
           "前端会用环形进度条展示给用户看",
           {"items": {"type": "array",
                       "items": {"type": "object",
                                  "properties": {
                                      "id": {"type": "string"},
                                      "title": {"type": "string"},
                                      "status": {"type": "string"}}}}},
           ["items"]),
        fn("todo_update",
           "更新某条 todo 的状态;status: pending/in_progress/completed",
           {"id": {"type": S}, "status": {"type": S}},
           ["id", "status"]),
        fn("list_dir", "列目录", {"path": {"type": S}}, []),
        fn("read_file", "读文件", {"path": {"type": S}}, ["path"]),
        fn("search_text", "在目录内搜文本",
           {"query": {"type": S}, "path": {"type": S},
            "exts": {"type": S, "description": "逗号分隔扩展名,可空"}},
           ["query"]),
        fn("web_search",
           "联网搜索(无需key)。查使用文档/库用法/报错/实时信息时主动用",
           {"query": {"type": S},
            "n": {"type": "integer", "description": "结果条数,默认5"}},
           ["query"]),
        fn("create_file", "新建文件(不存在才行)",
           {"path": {"type": S}, "content": {"type": S}}, ["path"]),
        fn("write_file", "写/覆盖文件",
           {"path": {"type": S}, "content": {"type": S}},
           ["path", "content"]),
        fn("edit_file", "精确替换文件中一段文本(old须唯一)",
           {"path": {"type": S}, "old": {"type": S}, "new": {"type": S}},
           ["path", "old", "new"]),
        fn("delete_path", "删除文件或目录", {"path": {"type": S}},
           ["path"]),
        fn("run_command", "在工作目录执行 shell 命令",
           {"command": {"type": S}}, ["command"]),
        fn("git_checkpoint", "提交一个 git 检查点",
           {"message": {"type": S}}, []),
        fn("git_rollback", "git reset --hard 到指定 commit",
           {"to": {"type": S}}, ["to"]),
        fn("run_tests", "运行配置的测试命令", {}, []),
    ]
