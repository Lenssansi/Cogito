"""Cogito 记忆机制 —— 1:1 复刻 Claude Code 的两层记忆。

== 静态层(COGITO.md 家族,广 → 窄,**拼接**进系统提示,不是覆盖)==
  1 托管策略   %PROGRAMDATA%\\Cogito\\COGITO.md (Win) / /etc/cogito/COGITO.md  —— 可选,通常无
  2 用户全局   ~/.cogito/COGITO.md
  3 项目根     <root>/COGITO.md 与 <root>/.cogito/COGITO.md
  4 项目私有   <root>/COGITO.local.md(建议 gitignore)
  + @import    任一 COGITO.md 内 `@相对路径` 内联展开(≤4 跳,相对引用文件解析)
  + 规则       <root>/.cogito/rules/*.md(无 paths: 前言的开场全量加载)

== 动态层(auto-memory,与静态**平行**)==
  目录 ~/.cogito/projects/<slug>/memory/  下:
    MEMORY.md          索引(开场加载前 200 行 / 25KB)
    <name>.md          每条一个事实,带 frontmatter(name/description/type)
  开场:加载 MEMORY.md 索引 + 按当前任务**相关性召回**若干条记忆全文。
  写入:由 memory_save/update/delete 工具维护(顺带自动维护索引,避免漂移)。

子目录 COGITO.md / 路径作用域 rules 的"读文件时懒注入"是下一刀,本模块先把
开场就该加载的静态层 + 动态层做全。
"""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path
from typing import Any

# ---------- 路径体系 ----------

HOME = Path(os.path.expanduser("~"))


def _cogito_home() -> Path:
    """用户级根:默认 ~/.cogito;可用环境变量 COGITO_HOME 覆盖(测试/自定义位置)。"""
    return Path(os.environ.get("COGITO_HOME") or (HOME / ".cogito"))


def user_global() -> Path:
    return _cogito_home() / "COGITO.md"             # 静态层 2


def projects_dir() -> Path:
    return _cogito_home() / "projects"              # 动态层各项目记忆根


def _managed_path() -> Path:
    """静态层 1:机器级托管策略(组织/IT 部署;个人机一般不存在)。"""
    if os.name == "nt":
        base = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        return Path(base) / "Cogito" / "COGITO.md"
    return Path("/etc/cogito/COGITO.md")


def _slug(root: str) -> str:
    """把项目根路径转成稳定的目录名(同 Claude Code 的 D--ai-myproject-Cogito)。"""
    p = str(Path(root).resolve())
    return re.sub(r"[^A-Za-z0-9]+", "-", p).strip("-") or "root"


def memory_dir(root: str) -> Path:
    return projects_dir() / _slug(root) / "memory"


def index_path(root: str) -> Path:
    return memory_dir(root) / "MEMORY.md"


# ---------- 通用读 + frontmatter ----------

def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.S)


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """极简 YAML frontmatter 解析(只取顶层 key: value,够记忆用)。返回 (meta, body)。"""
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith((" ", "\t", "#")):
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip().strip("'\"")
    return meta, m.group(2)


# ---------- 静态层:@import 展开 ----------

_IMPORT_RE = re.compile(r"(?m)^\s*@([^\s@`]+)\s*$")
_MAX_IMPORT_HOPS = 4


def _expand_imports(text: str, base_dir: Path, depth: int = 0) -> str:
    """把行首 `@相对路径` 替换成被引文件内容(相对引用文件解析,≤4 跳)。"""
    if depth >= _MAX_IMPORT_HOPS:
        return text

    def repl(mo: re.Match[str]) -> str:
        ref = mo.group(1)
        target = (base_dir / ref).resolve()
        sub = _read(target)
        if not sub:
            return f"<!-- @{ref}: 未找到 -->"
        return _expand_imports(sub, target.parent, depth + 1)

    return _IMPORT_RE.sub(repl, text)


def _load_one(path: Path) -> str:
    raw = _read(path)
    if not raw.strip():
        return ""
    return _expand_imports(raw, path.parent).strip()


def _load_rules(root: str) -> list[tuple[str, str]]:
    """<root>/.cogito/rules/*.md:无 `paths:` 前言的开场全量加载。
    带 paths: 的留到"读文件时"按路径注入(下一刀),开场不加载。"""
    rdir = Path(root) / ".cogito" / "rules"
    if not rdir.is_dir():
        return []
    out: list[tuple[str, str]] = []
    for f in sorted(rdir.glob("*.md")):
        meta, body = parse_frontmatter(_read(f))
        if meta.get("paths"):          # 路径作用域规则 → 开场不加载
            continue
        body = body.strip()
        if body:
            out.append((f.stem, body))
    return out


# 静态层各文件被拼接时的小标题,纯解释、便于模型理解每段来源
_STATIC_LABELS = {
    "managed": "托管策略(组织级)",
    "global": "用户全局偏好(~/.cogito/COGITO.md)",
    "project": "项目说明(项目根 COGITO.md)",
    "local": "项目私有(COGITO.local.md)",
}

_STATIC_TOTAL_CAP = 60000   # 静态层总字符上限,防撑爆上下文


def static_layer(root: str) -> str:
    """按 广→窄 顺序拼接 COGITO.md 家族 + 规则。返回要进系统提示的文本('' 表示无)。"""
    rt = Path(root)
    sources: list[tuple[str, str]] = [
        (_STATIC_LABELS["managed"], _load_one(_managed_path())),
        (_STATIC_LABELS["global"], _load_one(user_global())),
        (_STATIC_LABELS["project"],
         _load_one(rt / "COGITO.md") or _load_one(rt / ".cogito" / "COGITO.md")),
        (_STATIC_LABELS["local"], _load_one(rt / "COGITO.local.md")),
    ]
    parts: list[str] = []
    total = 0
    for label, txt in sources:
        if not txt:
            continue
        chunk = f"\n\n----- {label} -----\n{txt}"
        if total + len(chunk) > _STATIC_TOTAL_CAP:
            break
        parts.append(chunk)
        total += len(chunk)
    for name, body in _load_rules(root):
        chunk = f"\n\n----- 规则 {name} -----\n{body}"
        if total + len(chunk) > _STATIC_TOTAL_CAP:
            break
        parts.append(chunk)
        total += len(chunk)
    if not parts:
        return ""
    return ("【你的常驻指令(用户/项目写的,按 广→窄 拼接;矛盾时窄的优先)】"
            + "".join(parts))


# ---------- 静态层:子目录 COGITO.md / 路径规则 的"读文件时"懒加载 ----------

def _within(p: Path, root: Path) -> bool:
    try:
        return p == root or p.is_relative_to(root)
    except (ValueError, OSError):
        return False


def _glob_match(rel: str, pat: str) -> bool:
    """paths: 可写多个(逗号/空格分隔),任一 fnmatch 命中即可。"""
    for one in (pat or "").replace(",", " ").split():
        if fnmatch.fnmatch(rel, one):
            return True
    return False


def subdir_context(root: str, path: str | None,
                   loaded: set[str]) -> str | None:
    """读到 root **子目录**下的文件时,返回沿途(root 之下)尚未加载过的子目录
    COGITO.md + 命中该文件路径的路径作用域规则(.cogito/rules 带 paths:)。
    懒加载:每条按会话只注入一次(loaded 集合由宿主按会话持有);无新内容→None。"""
    if not path:
        return None
    rt = Path(root).resolve()
    try:
        p = Path(path)
        p = (rt / p if not p.is_absolute() else p).resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    if p != rt and not _within(p, rt):
        return None       # 读的是 root 之外的东西 → 没有"子目录"语义
    base = p if p.is_dir() else p.parent
    parts: list[str] = []
    # 1) root → base 链上各级子目录(不含 root,其 COGITO.md 已在静态层)
    chain: list[Path] = []
    cur = base
    while cur != rt and _within(cur, rt):
        chain.append(cur)
        if cur.parent == cur:
            break
        cur = cur.parent
    for d in reversed(chain):                       # 浅 → 深
        key = f"dir:{d}"
        if key in loaded:
            continue
        loaded.add(key)
        txt = _load_one(d / "COGITO.md")
        if txt:
            parts.append(f"\n\n----- 子目录 {d.relative_to(rt)}/COGITO.md "
                         f"-----\n{txt}")
    # 2) 路径作用域规则:.cogito/rules/*.md 带 paths:,命中该文件相对路径才注入
    try:
        rel = str(p.relative_to(rt)).replace("\\", "/")
    except ValueError:
        rel = ""
    rdir = rt / ".cogito" / "rules"
    if rel and rdir.is_dir():
        for f in sorted(rdir.glob("*.md")):
            rkey = f"rule:{f.name}"
            if rkey in loaded:
                continue
            meta, body = parse_frontmatter(_read(f))
            pat = meta.get("paths", "")
            if pat and _glob_match(rel, pat):
                loaded.add(rkey)
                body = body.strip()
                if body:
                    parts.append(f"\n\n----- 规则 {f.stem}(命中 {pat}) "
                                 f"-----\n{body}")
    if not parts:
        return None
    return ("【就近加载的目录指令/规则(因你读了该目录下的文件)】"
            + "".join(parts))


# ---------- 动态层:索引 + 召回 ----------

_INDEX_MAX_LINES = 200
_INDEX_MAX_BYTES = 25000


def load_index(root: str) -> str:
    """开场加载 MEMORY.md 索引(前 200 行 / 25KB)。"""
    txt = _read(index_path(root))
    if not txt.strip():
        return ""
    lines = txt.splitlines()[:_INDEX_MAX_LINES]
    out = "\n".join(lines)
    return out[:_INDEX_MAX_BYTES]


_WORD_RE = re.compile(r"[A-Za-z0-9]+|[一-鿿]")


def _tokens(s: str) -> set[str]:
    """英文按词、中文按单字切(无依赖的最简召回)。"""
    return {t.lower() for t in _WORD_RE.findall(s or "")}


def _iter_memories(root: str):
    mdir = memory_dir(root)
    if not mdir.is_dir():
        return
    for f in sorted(mdir.glob("*.md")):
        if f.name == "MEMORY.md":
            continue
        meta, body = parse_frontmatter(_read(f))
        yield f.stem, meta, body


def recall(root: str, query: str, k: int = 3) -> list[tuple[str, str]]:
    """按当前任务相关性召回记忆:description+name 与 query 的 token 重合度打分。
    返回 [(name, 全文)],至多 k 条,无相关返回 []。"""
    q = _tokens(query)
    if not q:
        return []
    scored: list[tuple[int, str, str]] = []
    for name, meta, body in _iter_memories(root):
        key = _tokens(meta.get("description", "") + " " + name)
        score = len(q & key)
        if score > 0:
            full = _read(memory_dir(root) / f"{name}.md").strip()
            scored.append((score, name, full))
    scored.sort(key=lambda x: -x[0])
    return [(n, full) for _, n, full in scored[:k]]


def dynamic_layer(root: str, query: str) -> str:
    """索引(常驻)+ 召回的若干条记忆全文(以背景信息形式)。"""
    idx = load_index(root)
    if not idx:
        return ""   # 还没有任何记忆
    parts = [
        "【你的记忆(动态层 —— 你过去自己记下的;是写时快照,引用前先对当前"
        "代码核实)。索引如下,需要某条细节用 memory_read 取全文】:\n" + idx
    ]
    for name, full in recall(root, query):
        if full:
            parts.append(f"\n\n[召回记忆 · {name}]\n{full}")
    return "".join(parts)


# ---------- 记忆纪律(镜像 Claude Code 的 # Memory 指令)----------

DISCIPLINE = (
    "【记忆机制】你有跨会话的持久记忆,存在本机一个目录里(用 memory_save/"
    "memory_update/memory_delete/memory_read 工具读写,无需也不能用普通文件"
    "工具碰它)。每条记忆 = 一个事实,带 name(kebab-case 短 slug)/ description"
    "(一句话摘要,**召回就靠它**,要写好)/ type:\n"
    "  user — 用户是谁(角色/专长/偏好);feedback — 用户对你工作方式的指导"
    "(必带 Why + 如何应用);project — 进行中的工作/目标/约束(代码和 git 里"
    "看不出来的;相对日期转绝对);reference — 外部资源指针(URL/看板/ticket)。\n"
    "**何时写**:学到**持久且非显然**的东西才记;只跟本次对话相关的别记;"
    "代码结构/改过的 bug/git 历史/已在常驻指令里的——都**别重复记**。"
    "先查有没有覆盖同一事的记忆,有就 memory_update 而不是新建;发现某条"
    "错了就 memory_delete。正文里可用 [[别的name]] 互链。"
)


def inject(root: str, task: str) -> str:
    """组装要追加到系统提示末尾的完整记忆区(静态层 + 纪律 + 动态层)。"""
    blocks = [b for b in (static_layer(root), DISCIPLINE,
                          dynamic_layer(root, task)) if b]
    return "\n\n".join(blocks)


# ---------- 写入助手(供 memory_* 工具)----------

_NAME_RE = re.compile(r"[^a-z0-9-]+")
_VALID_TYPES = {"user", "feedback", "project", "reference"}


def _safe_name(name: str) -> str:
    """把 name 规范成安全的 kebab-case slug —— 杜绝路径穿越(不许有 / \\ ..)。"""
    s = _NAME_RE.sub("-", (name or "").strip().lower()).strip("-")
    return s[:60] or "mem"


def _rebuild_index(root: str) -> None:
    """从各记忆文件的 frontmatter 重建 MEMORY.md 索引(自动维护,杜绝漂移)。"""
    lines = ["# Cogito 记忆索引(自动维护;每条一行)\n"]
    for name, meta, _body in _iter_memories(root):
        desc = meta.get("description", "").strip() or "(无摘要)"
        lines.append(f"- [{name}]({name}.md) — {desc}")
    index_path(root).write_text("\n".join(lines) + "\n", encoding="utf-8")


def save_memory(root: str, name: str, description: str,
                mtype: str, body: str) -> dict[str, Any]:
    """新建/覆盖一条记忆 + 重建索引。"""
    nm = _safe_name(name)
    t = mtype if mtype in _VALID_TYPES else "project"
    # description 压成单行:含换行会撑坏 frontmatter 与索引那一行(野行/丢数据)
    desc = " ".join(description.split())
    mdir = memory_dir(root)
    mdir.mkdir(parents=True, exist_ok=True)
    fm = (f"---\nname: {nm}\ndescription: {desc}\n"
          f"metadata:\n  type: {t}\n---\n\n{body.strip()}\n")
    (mdir / f"{nm}.md").write_text(fm, encoding="utf-8")
    _rebuild_index(root)
    return {"saved": nm, "type": t}


def update_memory(root: str, name: str, description: str = "",
                  mtype: str = "", body: str = "") -> dict[str, Any]:
    """更新已有记忆的某些字段(空=不改)。不存在则报错。"""
    nm = _safe_name(name)
    path = memory_dir(root) / f"{nm}.md"
    if not path.is_file():
        return {"error": f"记忆不存在:{nm}(新建请用 memory_save)"}
    meta, old_body = parse_frontmatter(_read(path))
    new_desc = description.strip() or meta.get("description", "")
    new_type = mtype if mtype in _VALID_TYPES else meta.get("type", "project")
    new_body = body.strip() or old_body.strip()
    return save_memory(root, nm, new_desc, new_type, new_body)


def delete_memory(root: str, name: str) -> dict[str, Any]:
    nm = _safe_name(name)
    path = memory_dir(root) / f"{nm}.md"
    if not path.is_file():
        return {"error": f"记忆不存在:{nm}"}
    path.unlink()
    _rebuild_index(root)
    return {"deleted": nm}


def read_memory(root: str, name: str) -> dict[str, Any]:
    nm = _safe_name(name)
    path = memory_dir(root) / f"{nm}.md"
    if not path.is_file():
        return {"error": f"记忆不存在:{nm}"}
    return {"name": nm, "content": _read(path)}
