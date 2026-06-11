"""联网搜索 —— 改用付费/免费的搜索 API(默认 Tavily)。

为什么换掉原来的 Bing/DDG HTML 抓取:HTML 改版/反爬频繁、关键词不精,
质量太差。Tavily 是专为 LLM 设计的搜索 API(api.tavily.com),返回
结构化结果,免费 1000 次/月对个人足够。

配置在 settings.json 的 `search` 段(独立于 providers!),由 config 的
get_search() / set_search() 维护。如 provider="off" 或 api_key 没填 →
直接返 [](不做查询),不再用 HTML 抓取兜底(已彻底清除)。
"""

from __future__ import annotations

from typing import Any

import httpx

from config import get_search


def _tavily_search(query: str, n: int, key: str,
                   proxy: str | None = None) -> list[dict[str, Any]]:
    """同步:POST api.tavily.com/search,返结构化结果。"""
    url = "https://api.tavily.com/search"
    payload = {
        "api_key": key,
        "query": query,
        "search_depth": "basic",  # advanced 更贵更慢,basic 够用
        "max_results": max(1, min(int(n), 10)),
        "include_answer": False,
        "include_raw_content": False,
    }
    try:
        with httpx.Client(timeout=15.0, proxy=proxy) as c:
            r = c.post(url, json=payload)
        if r.status_code != 200:
            return []
        data = r.json()
        out: list[dict[str, Any]] = []
        for item in (data.get("results") or [])[:n]:
            out.append({
                "title": str(item.get("title") or "")[:200],
                "snippet": str(item.get("content") or "")[:500],
                "url": str(item.get("url") or ""),
            })
        return out
    except (httpx.HTTPError, ValueError, KeyError):
        return []


async def _tavily_search_async(query: str, n: int, key: str,
                                proxy: str | None = None
                                ) -> list[dict[str, Any]]:
    """异步版,逻辑同步等价。"""
    url = "https://api.tavily.com/search"
    payload = {
        "api_key": key, "query": query, "search_depth": "basic",
        "max_results": max(1, min(int(n), 10)),
        "include_answer": False, "include_raw_content": False,
    }
    try:
        async with httpx.AsyncClient(timeout=15.0, proxy=proxy) as c:
            r = await c.post(url, json=payload)
        if r.status_code != 200:
            return []
        data = r.json()
        out: list[dict[str, Any]] = []
        for item in (data.get("results") or [])[:n]:
            out.append({
                "title": str(item.get("title") or "")[:200],
                "snippet": str(item.get("content") or "")[:500],
                "url": str(item.get("url") or ""),
            })
        return out
    except (httpx.HTTPError, ValueError, KeyError):
        return []


def _resolve() -> dict[str, Any]:
    """读 search 配置。"""
    cfg = get_search()
    return {
        "provider": str(cfg.get("provider", "tavily")).lower(),
        "api_key": str(cfg.get("api_key") or ""),
        "n": int(cfg.get("max_results") or 5),
        "proxy": None,
    }


async def web_search(query: str, n: int = 0) -> list[dict[str, Any]]:
    """异步联网搜索。失败/未配置都返 []。"""
    q = (query or "").strip()
    if not q:
        return []
    cfg = _resolve()
    use_n = n or cfg["n"]
    if cfg["provider"] == "tavily":
        if not cfg["api_key"]:
            return []
        return await _tavily_search_async(q, use_n, cfg["api_key"],
                                           proxy=cfg["proxy"])
    return []  # provider="off" 或未知


def web_search_sync(query: str, n: int = 0) -> list[dict[str, Any]]:
    """同步版(供 Agent 工具循环用)。"""
    q = (query or "").strip()
    if not q:
        return []
    cfg = _resolve()
    use_n = n or cfg["n"]
    if cfg["provider"] == "tavily":
        if not cfg["api_key"]:
            return []
        return _tavily_search(q, use_n, cfg["api_key"], proxy=cfg["proxy"])
    return []


def as_context(results: list[dict[str, Any]]) -> str:
    if not results:
        return ""
    lines = ["以下是联网搜索结果（可能有时效性，请据此作答并指出信息时间）："]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}\n   {r['snippet']}\n   {r['url']}")
    return "\n".join(lines)


def test_query(query: str = "ping") -> dict[str, Any]:
    """搜索配置自检。返回 {ok, provider, count, results?, error?}。"""
    cfg = _resolve()
    provider = cfg["provider"]
    if provider == "off":
        return {"ok": False, "provider": "off",
                "error": "联网搜索已关闭(provider=off)"}
    if not cfg["api_key"]:
        return {"ok": False, "provider": provider,
                "error": f"{provider} 未配置 api_key"}
    try:
        results = web_search_sync(query or "ping", 3)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "provider": provider, "error": str(e)[:200]}
    if not results:
        return {"ok": False, "provider": provider, "count": 0,
                "error": "0 条结果 —— 检查 key 是否正确"}
    return {"ok": True, "provider": provider,
            "count": len(results), "results": results}
