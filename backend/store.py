"""会话本地持久化(单用户)。

存 data/agent_sessions.json,已被 gitignore。统一会话(对话=Agent)的
messages/transcript/checkpoint 都在这里;旧 conversations.json 已随
页面合并退役(文件留盘不再读写)。
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from config import DATA_DIR

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
