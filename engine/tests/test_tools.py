"""ToolRegistry 测试:统一 FS 工具 + 边界全由 Scope 决定 + 每会话实例隔离。

重点用例:
- 相对路径基于 scope.cwd 解析;越界返回 error;临时授权放行(护栏一致性)
- AllowAllScope = 全盘语义(chatfs 文件模式)
- edit_file 唯一匹配 / create 拒绝覆盖 等编辑护栏
- todos 实例隔离 —— 两个并发会话互不串台(旧版模块全局的竞态修复证明)
- 自定义工具注册(web_search 这类由使用方注入)
- git 检查点/回滚(真实 tmp git 仓库)
"""

from __future__ import annotations

import subprocess

import pytest

from cogito_engine.scope import AllowAllScope, DirScope
from cogito_engine.tools import ToolRegistry


def _reg(tmp_path, **kw) -> ToolRegistry:
    scope = DirScope(cwd=str(tmp_path), allowed_roots=[str(tmp_path)])
    return ToolRegistry(scope, **kw)


# ---------- 路径解析与护栏 ----------

def test_relative_path_resolves_against_scope_cwd(tmp_path):
    reg = _reg(tmp_path)
    r = reg.run("create_file", {"path": "a.txt", "content": "hi"})
    assert r.get("created") is True
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "hi"
    r2 = reg.run("read_file", {"path": "a.txt"})
    assert r2["content"] == "hi"


def test_outside_scope_returns_error(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    outside = tmp_path / "other"
    outside.mkdir()
    (outside / "s.txt").write_text("secret", encoding="utf-8")
    scope = DirScope(cwd=str(root), allowed_roots=[str(root)])
    reg = ToolRegistry(scope)
    r = reg.run("read_file", {"path": str(outside / "s.txt")})
    assert "error" in r and "越出" in r["error"]


def test_temporary_grant_unlocks_outside(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    outside = tmp_path / "userfiles"
    outside.mkdir()
    (outside / "x.txt").write_text("data", encoding="utf-8")
    scope = DirScope(cwd=str(root), allowed_roots=[str(root)])
    reg = ToolRegistry(scope)
    assert "error" in reg.run("read_file", {"path": str(outside / "x.txt")})
    scope.grant_temporary([str(outside)])  # 用户点名 → 本轮放行
    assert reg.run("read_file",
                   {"path": str(outside / "x.txt")})["content"] == "data"


def test_allow_all_scope_full_disk(tmp_path):
    base = tmp_path / "session"
    base.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "y.txt").write_text("anywhere", encoding="utf-8")
    reg = ToolRegistry(AllowAllScope(cwd=str(base)))
    # 全盘:白名单外也能读;相对路径仍基于会话目录
    assert reg.run("read_file",
                   {"path": str(elsewhere / "y.txt")})["content"] == "anywhere"
    reg.run("create_file", {"path": "rel.txt", "content": "x"})
    assert (base / "rel.txt").exists()


# ---------- 编辑类护栏 ----------

def test_create_refuses_overwrite_write_overwrites(tmp_path):
    reg = _reg(tmp_path)
    assert reg.run("create_file", {"path": "f.txt", "content": "1"})["created"]
    assert "error" in reg.run("create_file", {"path": "f.txt", "content": "2"})
    r = reg.run("write_file", {"path": "f.txt", "content": "2"})
    assert r["overwritten"] is True
    assert (tmp_path / "f.txt").read_text(encoding="utf-8") == "2"


def test_edit_file_requires_unique_match(tmp_path):
    reg = _reg(tmp_path)
    (tmp_path / "e.txt").write_text("aaa bbb aaa", encoding="utf-8")
    assert "error" in reg.run(
        "edit_file", {"path": "e.txt", "old": "zzz", "new": "x"})  # 找不到
    assert "error" in reg.run(
        "edit_file", {"path": "e.txt", "old": "aaa", "new": "x"})  # 不唯一
    r = reg.run("edit_file", {"path": "e.txt", "old": "bbb", "new": "MID"})
    assert r["replaced"] is True
    assert (tmp_path / "e.txt").read_text(encoding="utf-8") == "aaa MID aaa"


def test_delete_path_file_and_dir(tmp_path):
    reg = _reg(tmp_path)
    (tmp_path / "gone.txt").write_text("x", encoding="utf-8")
    d = tmp_path / "subdir"
    d.mkdir()
    (d / "inner.txt").write_text("y", encoding="utf-8")
    assert reg.run("delete_path", {"path": "gone.txt"})["deleted"]
    assert reg.run("delete_path", {"path": "subdir"})["deleted"]
    assert not (tmp_path / "gone.txt").exists()
    assert not d.exists()


def test_search_text_skips_noise_dirs(tmp_path):
    reg = _reg(tmp_path)
    (tmp_path / "hit.py").write_text("needle here", encoding="utf-8")
    git = tmp_path / ".git"
    git.mkdir()
    (git / "noise.txt").write_text("needle in git", encoding="utf-8")
    r = reg.run("search_text", {"query": "needle"})
    assert len(r["matches"]) == 1
    assert "hit.py" in r["matches"][0]


# ---------- todos:实例隔离(竞态修复证明)----------

def test_todos_are_instance_isolated(tmp_path):
    """旧版用模块级全局 _TODOS,两个并发会话会互踩;现在每会话一个注册表。"""
    reg1 = _reg(tmp_path)
    reg2 = _reg(tmp_path)
    reg1.run("todo_set", {"items": [{"id": "t1", "title": "任务A"}]})
    assert [t["id"] for t in reg1.todos] == ["t1"]
    assert reg2.todos == []                       # 会话2 看不到会话1 的
    r = reg2.run("todo_update", {"id": "t1", "status": "completed"})
    assert "error" in r                           # 会话2 里没有 t1


def test_todo_flow(tmp_path):
    reg = _reg(tmp_path)
    r = reg.run("todo_set", {"items": [
        {"id": "a", "title": "第一步"},
        {"id": "b", "title": "第二步", "status": "doing"},
    ]})
    assert r["count"] == 2
    assert r["todos"][1]["status"] == "in_progress"  # doing 归一化
    r2 = reg.run("todo_update", {"id": "a", "status": "done"})
    assert r2["todos"][0]["status"] == "completed"


# ---------- 命令执行 ----------

def test_run_command_echo(tmp_path):
    reg = _reg(tmp_path)
    r = reg.run("run_command", {"command": "echo hello"})
    assert r["exit_code"] == 0
    assert "hello" in r["stdout"]


def test_run_tests_skipped_without_cmd(tmp_path):
    reg = _reg(tmp_path)
    assert reg.run("run_tests", {}).get("skipped") is True
    reg2 = _reg(tmp_path, test_cmd="echo ok")
    r = reg2.run("run_tests", {})
    assert r["exit_code"] == 0 and "ok" in r["stdout"]


# ---------- 注册表行为 ----------

def test_custom_tool_registration(tmp_path):
    reg = _reg(tmp_path)
    spec = {"type": "function", "function": {
        "name": "web_search", "description": "搜",
        "parameters": {"type": "object",
                       "properties": {"query": {"type": "string"}},
                       "required": ["query"]}}}
    reg.register("web_search", lambda query: {"results": [query]},
                 spec=spec, high_risk=False)
    assert reg.run("web_search", {"query": "q1"}) == {"results": ["q1"]}
    assert any(s["function"]["name"] == "web_search" for s in reg.specs())
    # 另一个注册表没有它(自定义工具也是实例级的)
    assert "error" in _reg(tmp_path).run("web_search", {"query": "x"})


def test_register_duplicate_raises(tmp_path):
    reg = _reg(tmp_path)
    with pytest.raises(ValueError):
        reg.register("read_file", lambda: {}, spec={"function": {}}, )


def test_unknown_tool_and_conservative_high_risk(tmp_path):
    reg = _reg(tmp_path)
    assert "error" in reg.run("nope", {})
    assert reg.is_high_risk("nope") is True       # 未知工具按高危(保守)


def test_high_risk_flags(tmp_path):
    reg = _reg(tmp_path)
    for risky in ("create_file", "write_file", "edit_file",
                  "delete_path", "run_command", "git_rollback"):
        assert reg.is_high_risk(risky) is True
    for safe in ("user_dirs", "list_dir", "read_file", "search_text",
                 "todo_set", "todo_update", "git_checkpoint", "run_tests"):
        assert reg.is_high_risk(safe) is False


def test_specs_exclude(tmp_path):
    reg = _reg(tmp_path)
    names = [s["function"]["name"] for s in reg.specs(exclude={"run_command"})]
    assert "run_command" not in names and "read_file" in names


def test_groups_subset(tmp_path):
    reg = _reg(tmp_path, groups={"fs"})
    names = [s["function"]["name"] for s in reg.specs()]
    assert "read_file" in names
    assert "run_command" not in names and "git_checkpoint" not in names
    assert "error" in reg.run("run_command", {"command": "echo x"})


# ---------- git 安全网(真实 tmp 仓库)----------

def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, timeout=30)


def test_git_checkpoint_and_rollback(tmp_path):
    _git(["init", "-b", "main"], tmp_path)
    _git(["config", "user.email", "t@t"], tmp_path)
    _git(["config", "user.name", "t"], tmp_path)
    (tmp_path / "code.py").write_text("v1", encoding="utf-8")
    reg = _reg(tmp_path)

    cp = reg.run("git_checkpoint", {"message": "起点"})
    assert cp.get("checkpoint")                  # 拿到 commit 哈希

    (tmp_path / "code.py").write_text("v2 改坏了", encoding="utf-8")
    _git(["add", "-A"], tmp_path)
    _git(["commit", "-m", "bad"], tmp_path)

    rb = reg.run("git_rollback", {"to": cp["checkpoint"]})
    assert rb.get("rolled_back_to") == cp["checkpoint"]
    assert (tmp_path / "code.py").read_text(encoding="utf-8") == "v1"


def test_git_rollback_removes_new_files_but_keeps_ignored(tmp_path):
    """回滚要把检查点之后**新建**的文件删掉(reset --hard 删不掉未跟踪),
    但 .gitignore 的文件(如用户私人速查)必须保留。"""
    _git(["init", "-b", "main"], tmp_path)
    _git(["config", "user.email", "t@t"], tmp_path)
    _git(["config", "user.name", "t"], tmp_path)
    (tmp_path / ".gitignore").write_text("secret.txt\n", encoding="utf-8")
    reg = _reg(tmp_path)
    cp = reg.run("git_checkpoint", {"message": "起点"})  # 提交 .gitignore

    # 检查点之后:新建一个普通文件 + 一个被忽略的私人文件
    (tmp_path / "new.py").write_text("agent 新建的", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("我的密钥", encoding="utf-8")

    rb = reg.run("git_rollback", {"to": cp["checkpoint"]})
    assert rb.get("rolled_back_to") == cp["checkpoint"]
    assert not (tmp_path / "new.py").exists()        # 新建文件被清掉
    assert (tmp_path / "secret.txt").exists()        # 被忽略的私人文件保留
    assert (tmp_path / "secret.txt").read_text(encoding="utf-8") == "我的密钥"


def test_git_checkpoint_fails_loudly_on_commit_failure(tmp_path, monkeypatch):
    """commit 失败时,git_checkpoint 必须报 error,而不是把旧 HEAD 当作新
    检查点返回(假回滚点 = 最危险的『安全网其实是空的且不自知』)。"""
    _git(["init", "-b", "main"], tmp_path)
    _git(["config", "user.email", "t@t"], tmp_path)
    _git(["config", "user.name", "t"], tmp_path)
    (tmp_path / "a.txt").write_text("v1", encoding="utf-8")
    _git(["add", "-A"], tmp_path)
    _git(["commit", "-m", "init"], tmp_path)
    head0 = _git(["rev-parse", "HEAD"], tmp_path).stdout.strip()

    reg = _reg(tmp_path)
    real_git = reg._git

    def fake_git(args):  # 让 commit 必失败(模拟 pre-commit 钩子/锁/磁盘满)
        if args and args[0] == "commit":
            return subprocess.CompletedProcess(args, 1, "", "commit blocked")
        return real_git(args)

    monkeypatch.setattr(reg, "_git", fake_git)
    cp = reg.run("git_checkpoint", {"message": "x"})
    assert "error" in cp                                       # 报错
    assert "checkpoint" not in cp                              # 不返回假点
    assert _git(["rev-parse", "HEAD"], tmp_path).stdout.strip() == head0


def test_user_dirs_tool(tmp_path):
    r = _reg(tmp_path).run("user_dirs", {})
    assert "home" in r and "desktop" in r
