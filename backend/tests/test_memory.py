"""记忆机制测试 —— 静态层(拼接/@import/规则)+ 动态层(存/读/召回/删)+ 防穿越。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import memory


def _setup_home(tmp_path, monkeypatch) -> Path:
    """把用户级根重定向到临时目录,绝不碰真实 ~/.cogito。"""
    home = tmp_path / "cogito-home"
    monkeypatch.setenv("COGITO_HOME", str(home))
    return home


# ---------- frontmatter ----------

def test_parse_frontmatter():
    text = "---\nname: foo\ndescription: 一句话\nmetadata:\n  type: project\n---\n\n正文\n"
    meta, body = memory.parse_frontmatter(text)
    assert meta["name"] == "foo"
    assert meta["description"] == "一句话"
    assert body.strip() == "正文"


def test_parse_frontmatter_none():
    meta, body = memory.parse_frontmatter("没有 frontmatter 的正文")
    assert meta == {}
    assert "没有" in body


# ---------- 静态层 ----------

def test_static_layer_concat_order(tmp_path, monkeypatch):
    home = _setup_home(tmp_path, monkeypatch)
    (home).mkdir(parents=True)
    (home / "COGITO.md").write_text("GLOBAL_PREF", encoding="utf-8")
    root = tmp_path / "proj"
    root.mkdir()
    (root / "COGITO.md").write_text("PROJECT_INFO", encoding="utf-8")
    (root / "COGITO.local.md").write_text("LOCAL_SECRET", encoding="utf-8")

    out = memory.static_layer(str(root))
    # 三层都在,且顺序 广→窄:global < project < local
    assert "GLOBAL_PREF" in out and "PROJECT_INFO" in out and "LOCAL_SECRET" in out
    assert out.index("GLOBAL_PREF") < out.index("PROJECT_INFO") < out.index("LOCAL_SECRET")


def test_static_layer_import_expand(tmp_path, monkeypatch):
    _setup_home(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    (root / "COGITO.md").write_text("开头\n@notes.md\n结尾", encoding="utf-8")
    (root / "notes.md").write_text("被导入的内容", encoding="utf-8")
    out = memory.static_layer(str(root))
    assert "被导入的内容" in out          # @import 被内联展开
    assert "@notes.md" not in out


def test_static_layer_rules_filter(tmp_path, monkeypatch):
    _setup_home(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    rdir = root / ".cogito" / "rules"
    rdir.mkdir(parents=True)
    (rdir / "always.md").write_text("无条件规则", encoding="utf-8")
    (rdir / "scoped.md").write_text(
        "---\npaths: src/**\n---\n按路径才加载的规则", encoding="utf-8")
    out = memory.static_layer(str(root))
    assert "无条件规则" in out               # 无 paths: → 开场加载
    assert "按路径才加载的规则" not in out    # 带 paths: → 开场不加载


def test_static_layer_empty(tmp_path, monkeypatch):
    _setup_home(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    assert memory.static_layer(str(root)) == ""   # 啥都没有 → 空串


# ---------- 动态层 ----------

def test_save_index_recall_roundtrip(tmp_path, monkeypatch):
    _setup_home(tmp_path, monkeypatch)
    root = str(tmp_path / "proj")
    r = memory.save_memory(
        root, "deepseek-key", "DeepSeek 测试密钥与默认 base_url",
        "reference", "base_url 默认 https://api.deepseek.com")
    assert r["saved"] == "deepseek-key" and r["type"] == "reference"

    idx = memory.load_index(root)
    assert "deepseek-key" in idx and "DeepSeek" in idx

    # description 含 "deepseek"/"base_url" → 相关 query 召回得到
    hits = memory.recall(root, "deepseek base_url 在哪")
    assert any(name == "deepseek-key" for name, _ in hits)
    # 完全不相关 → 不召回
    assert memory.recall(root, "天气怎么样") == []


def test_update_and_delete(tmp_path, monkeypatch):
    _setup_home(tmp_path, monkeypatch)
    root = str(tmp_path / "proj")
    memory.save_memory(root, "fact-x", "原描述", "project", "原正文")
    memory.update_memory(root, "fact-x", description="新描述")
    got = memory.read_memory(root, "fact-x")
    assert "新描述" in got["content"] and "原正文" in got["content"]  # 正文保留
    assert "fact-x" in memory.load_index(root)

    memory.delete_memory(root, "fact-x")
    assert "error" in memory.read_memory(root, "fact-x")
    assert "fact-x" not in memory.load_index(root)


def test_inject_combines_layers(tmp_path, monkeypatch):
    home = _setup_home(tmp_path, monkeypatch)
    home.mkdir(parents=True)
    (home / "COGITO.md").write_text("我的全局偏好", encoding="utf-8")
    root = str(tmp_path / "proj")
    memory.save_memory(root, "proj-goal", "项目目标 alpha", "project", "做记忆系统")
    out = memory.inject(root, "继续做 alpha 目标")
    assert "我的全局偏好" in out          # 静态层
    assert "记忆机制" in out               # 纪律(DISCIPLINE)
    assert "proj-goal" in out              # 动态层索引/召回


# ---------- 安全:防路径穿越 ----------

def test_multiline_description_collapsed(tmp_path, monkeypatch):
    _setup_home(tmp_path, monkeypatch)
    root = str(tmp_path / "proj")
    # 含换行的 description 不能撑坏 frontmatter / 索引
    memory.save_memory(root, "ml", "第一行\n第二行\n第三行", "project", "正文")
    meta, _ = memory.parse_frontmatter(memory.read_memory(root, "ml")["content"])
    assert meta["description"] == "第一行 第二行 第三行"   # 压成单行
    idx_lines = [ln for ln in memory.load_index(root).splitlines()
                 if ln.startswith("- [")]
    assert len(idx_lines) == 1 and "第二行" in idx_lines[0]  # 索引仍是一行


def test_subdir_context_loads_nested(tmp_path, monkeypatch):
    _setup_home(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    sub = root / "app" / "frontend"
    sub.mkdir(parents=True)
    (root / "app" / "COGITO.md").write_text("APP层说明", encoding="utf-8")
    (sub / "COGITO.md").write_text("FRONTEND层说明", encoding="utf-8")
    (sub / "x.tsx").write_text("code", encoding="utf-8")
    loaded: set = set()
    out = memory.subdir_context(str(root), str(sub / "x.tsx"), loaded)
    assert "APP层说明" in out and "FRONTEND层说明" in out   # 沿途两级都注入
    # 同会话再读同目录 → 已加载,不重复注入
    assert memory.subdir_context(str(root), str(sub / "x.tsx"), loaded) is None


def test_subdir_context_path_scoped_rule(tmp_path, monkeypatch):
    _setup_home(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    rdir = root / ".cogito" / "rules"
    rdir.mkdir(parents=True)
    (rdir / "py.md").write_text("---\npaths: *.py\n---\nPython专属规则",
                                encoding="utf-8")
    (root / "s.py").write_text("x=1", encoding="utf-8")
    (root / "s.txt").write_text("t", encoding="utf-8")
    hit = memory.subdir_context(str(root), str(root / "s.py"), set())
    assert hit and "Python专属规则" in hit          # .py 命中路径规则
    assert memory.subdir_context(str(root), str(root / "s.txt"), set()) is None


def test_subdir_context_outside_root(tmp_path, monkeypatch):
    _setup_home(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    out = memory.subdir_context(str(root), str(tmp_path / "outside.txt"), set())
    assert out is None                              # root 之外 → 无子目录语义


def test_save_records_dates(tmp_path, monkeypatch):
    _setup_home(tmp_path, monkeypatch)
    root = str(tmp_path / "proj")
    memory.save_memory(root, "d", "desc", "user", "body")
    meta, _ = memory.parse_frontmatter(memory.read_memory(root, "d")["content"])
    assert meta.get("created") and meta.get("updated")     # 记了日期


def test_dynamic_layer_stale_warning(tmp_path, monkeypatch):
    _setup_home(tmp_path, monkeypatch)
    root = str(tmp_path / "proj")
    memory.save_memory(root, "anchor", "占位让索引非空", "project", "x")
    old = ("---\nname: old-fact\ndescription: 旧事实\nupdated: 2020-01-01\n"
           "metadata:\n  type: project\n---\n\n这是个很旧的记忆")
    out = memory.dynamic_layer(root, "查", recalled=[("old-fact", old)])
    assert "⚠️" in out and "天前" in out                   # ≥2 天 → stale 警告


def test_load_index_truncation_warning(tmp_path, monkeypatch):
    _setup_home(tmp_path, monkeypatch)
    root = str(tmp_path / "proj")
    mdir = memory.memory_dir(root)
    mdir.mkdir(parents=True)
    (mdir / "MEMORY.md").write_text(
        "\n".join(f"- line{i}" for i in range(250)), encoding="utf-8")
    assert "截断" in memory.load_index(root)               # 超 200 行 → 警告


def test_recall_relevant_llm_and_fallback(tmp_path, monkeypatch):
    _setup_home(tmp_path, monkeypatch)
    root = str(tmp_path / "proj")
    memory.save_memory(root, "target-mem", "关于 alpha 的事", "project", "b")
    memory.save_memory(root, "other-mem", "无关的 beta", "project", "b")

    class _Sel:
        async def tool_complete(self, messages, specs):
            return {"content": '选好了:["target-mem"]', "tool_calls": []}

    hits = asyncio.run(memory.recall_relevant(root, "查 alpha", _Sel()))
    assert [n for n, _ in hits] == ["target-mem"]          # 选择器挑中
    kw = asyncio.run(memory.recall_relevant(root, "alpha", None))  # 无 provider
    assert any(n == "target-mem" for n, _ in kw)           # 退关键词,仍命中


def test_recall_relevant_multibracket_and_dedup(tmp_path, monkeypatch):
    _setup_home(tmp_path, monkeypatch)
    root = str(tmp_path / "proj")
    for n in ("mem-a", "mem-b"):
        memory.save_memory(root, n, f"about {n}", "project", "x")

    class _Chatty:   # 话痨:两段方括号 + 同名重复
        async def tool_complete(self, m, s):
            return {"content": '相关:["mem-a","mem-a"],忽略 ["junk"]',
                    "tool_calls": []}

    hits = asyncio.run(memory.recall_relevant(root, "q", _Chatty()))
    assert [n for n, _ in hits] == ["mem-a"]      # 取首段 + 去重,不含 junk/重复


def test_recall_relevant_parse_fail_falls_back(tmp_path, monkeypatch):
    _setup_home(tmp_path, monkeypatch)
    root = str(tmp_path / "proj")
    memory.save_memory(root, "alpha-mem", "关于 alpha", "project", "x")

    class _Junk:     # 没有任何合法 JSON 数组 → 解析失败
        async def tool_complete(self, m, s):
            return {"content": "我觉得没有合适的", "tool_calls": []}

    hits = asyncio.run(memory.recall_relevant(root, "alpha", _Junk()))
    assert any(n == "alpha-mem" for n, _ in hits)  # 解析失败 → 退关键词,仍命中


def test_recall_relevant_empty_respected(tmp_path, monkeypatch):
    _setup_home(tmp_path, monkeypatch)
    root = str(tmp_path / "proj")
    memory.save_memory(root, "alpha-mem", "关于 alpha", "project", "x")

    class _Empty:    # 模型明确给合法空数组 = 判定无相关
        async def tool_complete(self, m, s):
            return {"content": "[]", "tool_calls": []}

    assert asyncio.run(memory.recall_relevant(root, "alpha", _Empty())) == []


def test_safe_name_blocks_traversal(tmp_path, monkeypatch):
    _setup_home(tmp_path, monkeypatch)
    root = str(tmp_path / "proj")
    memory.save_memory(root, "../../evil", "x", "project", "y")
    mdir = memory.memory_dir(root)
    # 文件落在 memory 目录内(name 被净化),没有写到外面
    assert (mdir / "evil.md").is_file()
    assert not (tmp_path / "evil.md").exists()
    assert not (tmp_path.parent / "evil.md").exists()
