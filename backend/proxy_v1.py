"""聚合 API 代理 —— OpenAI 兼容的本地端点 /v1/*。

供其他工具(Cline、Cursor、NextChat 等)把 ai-helper 当统一接入口:
- 鉴权: Authorization: Bearer <proxy_api_key>(在「设置」页查看/重置)
- 模型路由: 客户端传 model = "provider_name/model_id" 走对应 provider;
  传裸 model_id 则按 active provider 匹配(没匹配上随便挑一个有该 model_id 的)
- 转发: 流式/非流式都直通到上游 base_url + "/v1/chat/completions"
- 不接 brain、不做联网注入、不调本地 ollama 直答 —— 这层就是单纯代理
"""

from __future__ import annotations

import json
import secrets
from typing import Any, AsyncIterator

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from config import (
    OLLAMA_PID,
    get_active_resolved,
    get_proxy_enabled,
    get_proxy_key,
    load_settings,
    resolve_choice,
)

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def _check_auth(request: Request) -> None:
    if not get_proxy_enabled():
        raise HTTPException(status_code=503, detail="本地 API 代理已关闭")
    # /v1/* 不经过 get_caller,这里自行执行远程门禁:
    # 远程未开启时,非本机一律拒(即使带对的 proxy key 也不行)。
    client_host = request.client.host if request.client else ""
    if client_host not in _LOOPBACK:
        if not load_settings().get("remote_enabled", False):
            raise HTTPException(status_code=403,
                                detail="远程访问未开启(本地 API 代理仅本机)")
    key = get_proxy_key()
    if not key:
        raise HTTPException(status_code=500, detail="proxy_api_key 未初始化")
    auth = request.headers.get("Authorization", "")
    expected = f"Bearer {key}"
    # 常量时间比较,杜绝 timing 爆破
    if not secrets.compare_digest(auth, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")


def list_models_data() -> list[dict[str, Any]]:
    """聚合所有 providers 的所有 preset 为 OpenAI 风格的 model 列表。
    id 形如 'DeepSeek/deepseek-v4-pro';同时 expose 裸 model id 方便不挑 provider 的客户端。"""
    s = load_settings()
    out: list[dict[str, Any]] = []
    seen_bare: set[str] = set()
    for p in s.get("providers", []):
        if not p.get("api_key"):
            continue
        for ps in p.get("presets", []) or []:
            mid = ps.get("model", "")
            if not mid:
                continue
            out.append({
                "id": f"{p.get('name', 'provider')}/{mid}",
                "object": "model",
                "owned_by": p.get("name", ""),
                "preset_label": ps.get("label", mid),
            })
            if mid not in seen_bare:
                seen_bare.add(mid)
                out.append({"id": mid, "object": "model",
                             "owned_by": p.get("name", "")})
    return out


def _split(model_str: str) -> tuple[str | None, str]:
    """'Provider/model' → (Provider, model);'model' → (None, model)"""
    if "/" in model_str:
        pn, mid = model_str.split("/", 1)
        return pn, mid
    return None, model_str


def _resolve_for_model(model_str: str) -> dict[str, Any] | None:
    """按 model 字符串挑一个有效的 resolved (provider+preset)。"""
    s = load_settings()
    pn, mid = _split(model_str)
    # 1) 指定了 provider 名
    if pn:
        for p in s.get("providers", []):
            if p.get("name") != pn or not p.get("api_key"):
                continue
            # 找匹配 mid 的 preset;没找到就用第一个,但 model 改成请求的 mid
            preset = next(
                (x for x in p.get("presets", []) if x.get("model") == mid),
                None,
            )
            label = preset["label"] if preset else (
                p["presets"][0]["label"] if p.get("presets") else ""
            )
            r = resolve_choice(p["id"], label)
            if r:
                r = dict(r)
                r["model"] = mid  # 强制用请求里的 model
                return r
    # 2) 裸 model: active 优先,再扫所有 providers
    act = get_active_resolved()
    if act and act.get("api_key") and (not pn or pn == act.get("provider_name")):
        if act.get("model") == mid:
            return act
    for p in s.get("providers", []):
        if not p.get("api_key"):
            continue
        if any(x.get("model") == mid for x in p.get("presets", [])):
            label = next(
                x["label"] for x in p["presets"] if x.get("model") == mid
            )
            r = resolve_choice(p["id"], label)
            if r:
                return r
    # 3) 全部失败,作 active fallback(model 用客户端的)
    if act and act.get("api_key"):
        r = dict(act)
        r["model"] = mid
        return r
    return None


def _normalize_base(base_url: str) -> str:
    b = (base_url or "").strip().rstrip("/")
    if not b.endswith("/v1"):
        b += "/v1"
    return b


async def proxy_chat_completions(request: Request):
    _check_auth(request)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="invalid JSON body")
    model_str = body.get("model", "") or ""
    if not model_str:
        raise HTTPException(status_code=400, detail="missing 'model'")
    resolved = _resolve_for_model(model_str)
    if not resolved:
        raise HTTPException(
            status_code=404,
            detail=f"no provider configured for model '{model_str}'",
        )
    # 用上游真实 model id 覆盖请求的 model
    body["model"] = resolved["model"]
    # 合并 preset 的 extra_body
    eb = resolved.get("extra_body") or {}
    for k, v in eb.items():
        body.setdefault(k, v)
    url = _normalize_base(resolved["base_url"]) + "/chat/completions"
    is_local = "11434" in resolved.get("base_url", "")  # 本地 Ollama 通常无需 key
    headers = {"Content-Type": "application/json"}
    key = resolved.get("api_key", "")
    if key and not is_local:
        headers["Authorization"] = f"Bearer {key}"
    elif is_local:
        # ollama OpenAI 兼容层接受空 key 或任意 key
        headers["Authorization"] = "Bearer ollama"

    is_stream = bool(body.get("stream", False))
    timeout = httpx.Timeout(300.0, connect=20.0)
    if is_stream:
        async def _gen() -> AsyncIterator[bytes]:
            async with httpx.AsyncClient(timeout=timeout) as c:
                async with c.stream(
                    "POST", url, headers=headers, json=body,
                ) as resp:
                    if resp.status_code != 200:
                        data = await resp.aread()
                        yield (
                            f"data: {json.dumps({'error': data.decode('utf-8', 'replace')})}\n\n"
                        ).encode("utf-8")
                        return
                    async for chunk in resp.aiter_bytes():
                        yield chunk
        return StreamingResponse(_gen(), media_type="text/event-stream")

    async with httpx.AsyncClient(timeout=timeout) as c:
        try:
            r = await c.post(url, headers=headers, json=body)
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"upstream {e}")
    return JSONResponse(content=r.json(), status_code=r.status_code)


# 兼容 OLLAMA_PID(虽然我们已删合成,但有些资料代码留这个常量) ---
_ = OLLAMA_PID
