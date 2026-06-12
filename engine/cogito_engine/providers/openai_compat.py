"""OpenAI 兼容 provider —— 覆盖 DeepSeek / Moonshot / 智谱 / Ollama 等绝大多数端点。

与应用解耦的两个注入缝:
- on_usage(provider_id, provider_name, usage_dict):拿到上游 usage 时回调,
  使用方接自己的记账;不传则不记。回调内的异常被吞,绝不影响主流程。
- transport:透传给 httpx(测试用 MockTransport 离线测,生产不传走真网络)。
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator, Callable

import httpx

from .base import ChatMessage, ProviderError

UsageHook = Callable[[str, str, dict], None]


def _normalize_base(base_url: str) -> str:
    """统一成 .../v1,兼容填 https://api.deepseek.com 或 .../v1。"""
    b = (base_url or "").strip().rstrip("/")
    if not b:
        raise ProviderError("未配置 base_url")
    if not b.endswith("/v1"):
        b = b + "/v1"
    return b


class OpenAICompatProvider:
    def __init__(
        self,
        cfg: dict[str, Any],
        *,
        on_usage: UsageHook | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = cfg.get("base_url", "")
        self.api_key = cfg.get("api_key", "")
        self.model = cfg.get("model", "")
        self.extra_body = cfg.get("extra_body", {}) or {}
        self.provider_id = cfg.get("provider_id", "")
        self.provider_name = cfg.get("provider_name", "")
        self._on_usage = on_usage
        self._transport = transport

    def _client(self, timeout: httpx.Timeout) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, transport=self._transport)

    def _record(self, usage: dict | None) -> None:
        """记一次用量;无回调/无 usage 字段则忽略,绝不影响主流程。"""
        if not usage or self._on_usage is None:
            return
        try:
            self._on_usage(self.provider_id, self.provider_name, usage)
        except Exception:  # noqa: BLE001
            pass

    async def stream_chat(
        self, messages: list[ChatMessage]
    ) -> AsyncIterator[tuple[str, str]]:
        if not self.api_key:
            raise ProviderError("未配置 API key")
        if not self.model:
            raise ProviderError("未配置 model")

        url = _normalize_base(self.base_url) + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        # 预设的额外参数(如 DeepSeek thinking、reasoning_effort)合并进请求体
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            # 让上游在尾包附带 usage(DeepSeek/OpenAI 等支持;不支持的服务
            # 通常忽略该字段,拿不到就不计,绝不影响对话)
            "stream_options": {"include_usage": True},
            **self.extra_body,
        }

        timeout = httpx.Timeout(60.0, connect=10.0)
        try:
            async with self._client(timeout) as client:
                async with client.stream(
                    "POST", url, headers=headers, json=payload
                ) as resp:
                    if resp.status_code != 200:
                        body = (await resp.aread()).decode("utf-8", "replace")
                        raise ProviderError(
                            f"上游返回 {resp.status_code}:{body[:500]}"
                        )
                    last_usage = None
                    async for line in resp.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            obj = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        if obj.get("usage"):
                            last_usage = obj["usage"]
                        choices = obj.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}
                        # 思考模型(DeepSeek thinking 等)把推理放 reasoning_content
                        rc = delta.get("reasoning_content")
                        if rc:
                            yield ("reasoning", rc)
                        piece = delta.get("content")
                        if piece:
                            yield ("answer", piece)
                    self._record(last_usage)
        except httpx.RequestError as e:
            raise ProviderError(f"网络错误:连不上 {self.base_url}({e})") from e

    async def tool_complete(
        self, messages: list[dict], tools: list[dict]
    ) -> dict:
        """非流式、带 function-calling 的一轮补全。
        返回 {content, tool_calls:[{id,name,arguments(dict)}]}。供 Agent 循环用。"""
        if not self.api_key:
            raise ProviderError("未配置 API key")
        url = _normalize_base(self.base_url) + "/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            # 空 tools 不传:部分上游对 "tools": [] 报错(连通性自检会用到)
            **({"tools": tools} if tools else {}),
            **self.extra_body,
        }
        try:
            async with self._client(
                httpx.Timeout(120.0, connect=10.0)
            ) as c:
                r = await c.post(
                    url,
                    headers={"Authorization": f"Bearer {self.api_key}",
                             "Content-Type": "application/json"},
                    json=payload,
                )
        except httpx.RequestError as e:
            raise ProviderError(f"网络错误:{e}") from e
        if r.status_code != 200:
            raise ProviderError(f"上游 {r.status_code}:{r.text[:500]}")
        body = r.json()
        self._record(body.get("usage"))
        msg = body["choices"][0]["message"]
        calls = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append({"id": tc.get("id", ""),
                          "name": fn.get("name", ""), "arguments": args})
        return {"content": msg.get("content") or "", "tool_calls": calls}
