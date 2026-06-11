"""Token 用量统计：按 provider 累计 调用次数 + tokens，落 data/usage.json。

模型每轮返回的 usage 字段（prompt/completion/total tokens）即时累加。
流式补全靠 stream_options.include_usage 拿尾包 usage；非流式直接读 usage。
本地 Ollama 多数不返 usage，则该次不计（次数也不计，避免脏数据）。
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone

from config import DATA_DIR

_USAGE_PATH = DATA_DIR / "usage.json"
_LOCK = threading.Lock()
_EMPTY = {"providers": {}, "updated": ""}


def _load() -> dict:
    try:
        return json.loads(_USAGE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {"providers": {}, "updated": ""}


def _save(d: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _USAGE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    os.replace(tmp, _USAGE_PATH)


def record(provider_id: str, provider_name: str,
           usage: dict | None) -> None:
    """累加一次调用的 usage。usage 为空/无 token 字段则跳过（不计次）。"""
    if not usage:
        return
    pt = int(usage.get("prompt_tokens") or 0)
    ct = int(usage.get("completion_tokens") or 0)
    tt = int(usage.get("total_tokens") or (pt + ct))
    if pt == 0 and ct == 0 and tt == 0:
        return
    key = provider_id or provider_name or "unknown"
    with _LOCK:
        d = _load()
        provs = d.setdefault("providers", {})
        e = provs.setdefault(key, {
            "name": provider_name or key, "calls": 0,
            "prompt_tokens": 0, "completion_tokens": 0,
            "total_tokens": 0,
        })
        e["name"] = provider_name or e.get("name") or key
        e["calls"] += 1
        e["prompt_tokens"] += pt
        e["completion_tokens"] += ct
        e["total_tokens"] += tt
        d["updated"] = datetime.now(timezone.utc).isoformat(
            timespec="seconds")
        _save(d)


def get_usage() -> dict:
    d = _load()
    provs = d.get("providers", {})
    rows = sorted(provs.values(),
                  key=lambda r: r.get("total_tokens", 0), reverse=True)
    totals = {
        "calls": sum(r.get("calls", 0) for r in provs.values()),
        "prompt_tokens": sum(r.get("prompt_tokens", 0)
                             for r in provs.values()),
        "completion_tokens": sum(r.get("completion_tokens", 0)
                                 for r in provs.values()),
        "total_tokens": sum(r.get("total_tokens", 0)
                            for r in provs.values()),
    }
    return {"rows": rows, "totals": totals,
            "updated": d.get("updated", "")}


def reset_usage() -> dict:
    with _LOCK:
        _save({"providers": {}, "updated": ""})
    return get_usage()
