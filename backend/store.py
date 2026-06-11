"""对话本地持久化（单用户，无需按登录区分）。

存 data/conversations.json，已被 gitignore。结构：{ cid: {id,title,updated,messages} }。
P3 的「跨 API 上下文连贯」是另一回事（滚动摘要），这里只负责存得住、找得回。
"""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

from config import DATA_DIR

CONV_PATH = DATA_DIR / "conversations.json"


def _load() -> dict[str, Any]:
    if CONV_PATH.exists():
        try:
            return json.loads(CONV_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save(d: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONV_PATH.write_text(
        json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def list_convs() -> list[dict[str, Any]]:
    items = _load().values()
    summaries = [
        {"id": c["id"], "title": c.get("title", "(未命名)"),
         "updated": c.get("updated", 0)}
        for c in items
    ]
    return sorted(summaries, key=lambda x: x["updated"], reverse=True)


def get_conv(cid: str) -> dict[str, Any] | None:
    return _load().get(cid)


def upsert_conv(cid: str, title: str, messages: list[dict],
                web: bool = False, file: bool = False) -> dict[str, Any]:
    d = _load()
    conv = {
        "id": cid,
        "title": (title or "(未命名)")[:40],
        "updated": time.time(),
        "messages": messages,
        "web": bool(web),    # 该对话联网开关
        "file": bool(file),  # 该对话「文件」模式开关
    }
    d[cid] = conv
    _save(d)
    return conv


def delete_conv(cid: str) -> bool:
    d = _load()
    if cid in d:
        del d[cid]
        _save(d)
        return True
    return False


# ===== 编程 Agent 会话持久化（结构更重：含 messages/transcript/checkpoint）=====
AGENT_PATH = DATA_DIR / "agent_sessions.json"


def _aload() -> dict[str, Any]:
    if AGENT_PATH.exists():
        try:
            return json.loads(AGENT_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _asave(d: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = AGENT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    os.replace(tmp, AGENT_PATH)


def list_agent() -> list[dict[str, Any]]:
    out = [{"id": s["id"], "title": s.get("title", "(未命名)"),
            "updated": s.get("updated", 0), "cwd": s.get("cwd", "")}
           for s in _aload().values()]
    return sorted(out, key=lambda x: x["updated"], reverse=True)


def get_agent(sid: str) -> dict[str, Any] | None:
    return _aload().get(sid)


def save_agent(sid: str, payload: dict[str, Any]) -> None:
    d = _aload()
    payload = {**payload, "id": sid, "updated": time.time()}
    d[sid] = payload
    _asave(d)


def delete_agent(sid: str) -> bool:
    d = _aload()
    if sid in d:
        del d[sid]
        _asave(d)
        return True
    return False
