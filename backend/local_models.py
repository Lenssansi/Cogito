"""本地 Ollama 状态探测(供设置页「组件与更新」面板显示)。

brain(本地小模型路由/直答/摘要)已随页面合并退役;这里只保留状态查询。
"""

from __future__ import annotations

from typing import Any

import httpx

from config import get_ollama


async def ollama_status() -> dict[str, Any]:
    cfg = get_ollama()
    base = cfg["base_url"].rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=4.0) as c:
            r = await c.get(f"{base}/api/tags")
            if r.status_code != 200:
                return {"reachable": False, "models": [],
                        "model": cfg["model"]}
            models = [m.get("name", "") for m in r.json().get("models", [])]
            return {"reachable": True, "models": models,
                    "model": cfg["model"]}
    except httpx.RequestError:
        return {"reachable": False, "models": [], "model": cfg["model"]}
