"""统一工具系统:每个会话一个 ToolRegistry 实例,无模块级可变全局。

设计要点:
- **一套 FS 工具,边界差异全部由注入的 Scope 决定**:DirScope(白名单 +
  临时授权,编程 Agent)/ AllowAllScope(全盘,文件助手)。工具代码只有
  一份,新增工具自动两种模式可用。
- **实例化状态**:todos、正在跑的子进程都挂在实例上 —— 修复旧版模块级
  全局(_TODOS/_EXTRA_PATHS/_PROCS)在并发会话下互踩的竞态。
- 内置工具按组开启:misc / todo / fs / exec / git;register() 注册自定义
  工具(如使用方注入的 web_search)。
- run_command 用 Popen 并登记到实例,kill_running() 可从外部立刻终止
  (供会话取消用)。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import userdirs as _userdirs
from .types import Scope

MAX_READ = 200_000  # 单文件读取上限,防爆上下文
CMD_TIMEOUT = 120

ALL_GROUPS = frozenset({"misc", "todo", "fs", "exec", "git"})


class ToolError(Exception):
    pass


def _decode(b: bytes) -> str:
    """utf-8 失败回退 GB18030/UTF-16,消除中文 Windows 上的乱码。"""
    for enc in ("utf-8", "gb18030", "utf-16"):
        try:
            return b.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return b.decode("latin-1", "replace")


def _norm_status(s: str | None) -> str:
    v = (s or "pending").strip().lower()
    if v in ("done", "complete", "completed", "✓"):
        return "completed"
    if v in ("doing", "active", "running", "in_progress", "wip"):
        return "in_progress"
    return "pending"


S = "string"


def _fn(name: str, desc: str, props: dict, req: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name, "description": desc,
            "parameters": {"type": "object", "properties": props,
                           "required": req},
        },
    }


@dataclass
class _Tool:
    handler: Callable[..., dict]
    spec: dict
    high_risk: bool


def git_init(path: str) -> dict[str, Any]:
    """在指定目录 git init(装配层端点用;不注册给模型)。"""
    p = Path(path)
    if not p.is_dir():
        raise ToolError(f"不是目录:{p}")
    if (p / ".git").exists():
        return {"path": str(p), "already_git": True}
    r = subprocess.run(["git", "init"], cwd=str(p), capture_output=True,
                       text=True, timeout=30)
    if r.returncode != 0:
        raise ToolError(f"git init 失败:{r.stderr.strip()}")
    return {"path": str(p), "initialized": True}


class ToolRegistry:
    def __init__(
        self,
        scope: Scope,
        *,
        groups: set[str] | frozenset[str] | None = None,
        test_cmd: str = "",
        todos: list[dict[str, Any]] | None = None,
    ) -> None:
        self.scope = scope
        self.test_cmd = test_cmd
        self.todos: list[dict[str, Any]] = list(todos or [])
        self._tools: dict[str, _Tool] = {}
        self._proc: subprocess.Popen | None = None
        self._proc_lock = threading.Lock()
        self._register_builtins(
            ALL_GROUPS if groups is None else frozenset(groups)
        )

    # ---------- 公共 API ----------

    def register(self, name: str, handler: Callable[..., dict], *,
                 spec: dict, high_risk: bool = False) -> None:
        """注册自定义工具(如使用方的 web_search)。重名直接报错,防误覆盖。"""
        if name in self._tools:
            raise ValueError(f"工具已存在:{name}")
        self._tools[name] = _Tool(handler, spec, high_risk)

    def is_high_risk(self, name: str) -> bool:
        t = self._tools.get(name)
        return True if t is None else t.high_risk  # 未知工具按高危(保守)

    def run(self, name: str, args: dict[str, Any] | None) -> dict[str, Any]:
        t = self._tools.get(name)
        if t is None:
            return {"error": f"未知工具:{name}"}
        try:
            return t.handler(**(args or {}))
        except ToolError as e:
            return {"error": str(e)}
        except TypeError as e:
            return {"error": f"参数错误:{e}"}
        except Exception as e:  # noqa: BLE001
            return {"error": f"执行失败:{e}"}

    def specs(self, exclude: set[str] | frozenset[str] = frozenset()
              ) -> list[dict]:
        return [t.spec for n, t in self._tools.items() if n not in exclude]

    def kill_running(self) -> bool:
        """终止本会话当前正在跑的子进程(供外部取消用)。"""
        with self._proc_lock:
            p, self._proc = self._proc, None
        if p is None:
            return False
        try:
            p.kill()
        except OSError:
            pass
        return True

    # ---------- 路径解析(护栏唯一入口)----------

    def _safe(self, path: str) -> Path:
        if not path:
            raise ToolError("路径为空")
        p = path if os.path.isabs(path) else os.path.join(
            self.scope.cwd, path)
        if not self.scope.is_allowed(p):
            raise ToolError(
                f"拒绝:路径越出授权范围 → {path}(在你的请求中明确写出"
                "该绝对路径,可获本轮临时授权)"
            )
        return Path(p).resolve()

    # ---------- 内置工具 ----------

    def _t_user_dirs(self) -> dict[str, Any]:
        return _userdirs.user_dirs()

    def _t_list_dir(self, path: str = ".") -> dict[str, Any]:
        p = self._safe(path)
        if not p.exists():
            raise ToolError(f"不存在:{p}")
        if not p.is_dir():
            raise ToolError(f"不是目录:{p}")
        items = [("dir " if e.is_dir() else "file") + e.name
                 for e in sorted(p.iterdir())]
        return {"path": str(p), "entries": items[:500]}

    def _t_read_file(self, path: str) -> dict[str, Any]:
        p = self._safe(path)
        if not p.is_file():
            raise ToolError(f"不是文件或不存在:{p}")
        data = _decode(p.read_bytes())
        return {"path": str(p), "content": data[:MAX_READ],
                "truncated": len(data) > MAX_READ}

    def _t_search_text(self, query: str, path: str = ".",
                       exts: str = "") -> dict[str, Any]:
        base = self._safe(path)
        ext_set = {e.strip().lstrip(".") for e in exts.split(",")
                   if e.strip()}
        hits: list[str] = []
        for root, _dirs, files in os.walk(base):
            if any(s in root for s in
                   (".git", "node_modules", ".venv", "__pycache__")):
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

    def _t_create_file(self, path: str, content: str = "") -> dict[str, Any]:
        p = self._safe(path)
        if p.exists():
            raise ToolError(f"已存在,拒绝覆盖(用 write_file):{p}")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return {"path": str(p), "created": True}

    def _t_write_file(self, path: str, content: str) -> dict[str, Any]:
        p = self._safe(path)
        existed = p.exists()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return {"path": str(p), "overwritten": existed}

    def _t_edit_file(self, path: str, old: str, new: str) -> dict[str, Any]:
        p = self._safe(path)
        if not p.is_file():
            raise ToolError(f"不是文件:{p}")
        text = _decode(p.read_bytes())
        n = text.count(old)
        if n == 0:
            raise ToolError("未找到要替换的 old 文本(需逐字精确匹配)")
        if n > 1:
            raise ToolError(f"old 文本出现 {n} 次不唯一,请给更长的上下文")
        p.write_text(text.replace(old, new, 1), encoding="utf-8")
        return {"path": str(p), "replaced": True}

    def _t_delete_path(self, path: str) -> dict[str, Any]:
        p = self._safe(path)
        if not p.exists():
            raise ToolError(f"不存在:{p}")
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
        return {"path": str(p), "deleted": True}

    def _t_run_command(self, command: str, cwd: str = "") -> dict[str, Any]:
        if not command or not command.strip():
            raise ToolError("命令为空")
        work = cwd or self.scope.cwd
        if not self.scope.is_allowed(work):
            raise ToolError(f"工作目录越出授权范围:{work}")
        workp = Path(work)
        if not workp.is_dir():
            raise ToolError(f"工作目录不存在:{work}")
        work = str(workp.resolve())
        try:
            proc = subprocess.Popen(
                command, shell=True, cwd=work,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, errors="replace",
                creationflags=(0x08000000 if os.name == "nt" else 0),
            )
        except OSError as e:
            raise ToolError(f"启动失败:{e}") from None
        with self._proc_lock:
            self._proc = proc
        try:
            try:
                out, err = proc.communicate(timeout=CMD_TIMEOUT)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    out, err = proc.communicate(timeout=3)
                except subprocess.TimeoutExpired:
                    out, err = "", ""
                raise ToolError(f"命令超时(>{CMD_TIMEOUT}s),已强制终止") \
                    from None
        finally:
            with self._proc_lock:
                self._proc = None
        # 被外部 kill_running 终止时 returncode 是负数(信号)
        killed = proc.returncode is not None and proc.returncode < 0
        return {"exit_code": proc.returncode, "stdout": (out or "")[-8000:],
                "stderr": (err or "")[-4000:], "cwd": work, "killed": killed}

    def _t_run_tests(self) -> dict[str, Any]:
        cmd = (self.test_cmd or "").strip()
        if not cmd:
            return {"skipped": True, "reason": "未配置测试命令"}
        return {"test_cmd": cmd, **self._t_run_command(cmd)}

    # ---------- git 安全网 ----------

    def _git(self, args: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args], cwd=self.scope.cwd, capture_output=True,
            text=True, timeout=60, errors="replace",
        )

    def _t_git_checkpoint(self, message: str = "cogito checkpoint"
                          ) -> dict[str, Any]:
        cwd = self.scope.cwd
        if not self.scope.is_allowed(cwd):
            raise ToolError("工作目录越出授权范围")
        if not (Path(cwd) / ".git").exists():
            raise ToolError("当前工作目录不是 git 仓库(可先 git init)")
        self._git(["add", "-A"])
        head_before = self._git(["rev-parse", "HEAD"]).stdout.strip()
        c = self._git(["commit", "-m", message, "--allow-empty"])
        head = self._git(["rev-parse", "HEAD"]).stdout.strip()
        return {"checkpoint": head, "prev": head_before,
                "info": c.stdout.strip() or c.stderr.strip()}

    def _t_git_rollback(self, to: str) -> dict[str, Any]:
        if not to:
            raise ToolError("缺少回滚目标 commit")
        r = self._git(["reset", "--hard", to])
        if r.returncode != 0:
            raise ToolError(f"回滚失败:{r.stderr.strip()}")
        return {"rolled_back_to": to, "info": r.stdout.strip()}

    # ---------- TODO 清单(实例状态)----------

    def _t_todo_set(self, items: list[dict[str, Any]] | None = None
                    ) -> dict[str, Any]:
        out: list[dict[str, Any]] = []
        for i, it in enumerate(items or []):
            if not isinstance(it, dict):
                continue
            title = str(it.get("title") or "").strip()
            if not title:
                continue
            out.append({
                "id": str(it.get("id") or f"t{i + 1}"),
                "title": title[:200],
                "status": _norm_status(it.get("status")),
            })
        self.todos = out
        return {"todos": out, "count": len(out)}

    def _t_todo_update(self, id: str, status: str) -> dict[str, Any]:
        target = _norm_status(status)
        for t in self.todos:
            if t["id"] == id:
                t["status"] = target
                return {"todos": list(self.todos)}
        return {"error": f"未找到 todo id={id}", "todos": list(self.todos)}

    # ---------- 内置注册 ----------

    def _register_builtins(self, groups: frozenset[str]) -> None:
        def add(group: str, name: str, handler: Callable[..., dict],
                spec: dict, high_risk: bool) -> None:
            if group in groups:
                self._tools[name] = _Tool(handler, spec, high_risk)

        add("misc", "user_dirs", self._t_user_dirs, _fn(
            "user_dirs",
            "取本机真实 用户名/主目录/桌面/下载/文档 绝对路径"
            "(涉及用户目录先调它,别猜)", {}, []), False)
        add("todo", "todo_set", self._t_todo_set, _fn(
            "todo_set",
            "(非简单任务用)开局列出 3-8 项待办,覆盖式重设。"
            "每项:{id,title,status: pending|in_progress|completed}",
            {"items": {"type": "array",
                       "items": {"type": "object",
                                 "properties": {
                                     "id": {"type": S},
                                     "title": {"type": S},
                                     "status": {"type": S}}}}},
            ["items"]), False)
        add("todo", "todo_update", self._t_todo_update, _fn(
            "todo_update",
            "更新某条 todo 的状态;status: pending/in_progress/completed",
            {"id": {"type": S}, "status": {"type": S}},
            ["id", "status"]), False)
        add("fs", "list_dir", self._t_list_dir, _fn(
            "list_dir", "列目录(相对路径基于工作目录)",
            {"path": {"type": S}}, []), False)
        add("fs", "read_file", self._t_read_file, _fn(
            "read_file", "读文件", {"path": {"type": S}}, ["path"]), False)
        add("fs", "search_text", self._t_search_text, _fn(
            "search_text", "在目录内搜文本",
            {"query": {"type": S}, "path": {"type": S},
             "exts": {"type": S, "description": "逗号分隔扩展名,可空"}},
            ["query"]), False)
        add("fs", "create_file", self._t_create_file, _fn(
            "create_file", "新建文件(不存在才行)",
            {"path": {"type": S}, "content": {"type": S}}, ["path"]), True)
        add("fs", "write_file", self._t_write_file, _fn(
            "write_file", "写/覆盖文件",
            {"path": {"type": S}, "content": {"type": S}},
            ["path", "content"]), True)
        add("fs", "edit_file", self._t_edit_file, _fn(
            "edit_file", "精确替换文件中一段文本(old须唯一)",
            {"path": {"type": S}, "old": {"type": S}, "new": {"type": S}},
            ["path", "old", "new"]), True)
        add("fs", "delete_path", self._t_delete_path, _fn(
            "delete_path", "删除文件或目录", {"path": {"type": S}},
            ["path"]), True)
        add("exec", "run_command", self._t_run_command, _fn(
            "run_command",
            "在工作目录执行 shell 命令(可指定 cwd)",
            {"command": {"type": S},
             "cwd": {"type": S, "description": "工作目录(可空)"}},
            ["command"]), True)
        add("git", "git_checkpoint", self._t_git_checkpoint, _fn(
            "git_checkpoint", "提交一个 git 检查点",
            {"message": {"type": S}}, []), False)
        add("git", "git_rollback", self._t_git_rollback, _fn(
            "git_rollback", "git reset --hard 到指定 commit",
            {"to": {"type": S}}, ["to"]), True)
        add("exec", "run_tests", self._t_run_tests, _fn(
            "run_tests", "运行配置的测试命令", {}, []), False)
