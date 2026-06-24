"""Provider 解析测试 —— 全部离线:httpx.MockTransport 喂假响应,不连网。

覆盖:tool_calls 解析 / 坏 JSON 容错 / 非 200 报错 / 缺 key 先验 /
base_url 归一化 / 空 tools 不传 / usage 回调(含回调抛错不影响主流程)/
工厂分发 / 流式逐段产出(answer + reasoning)。
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from cogito_engine.providers import (
    OpenAICompatProvider,
    ProviderError,
    get_provider,
)


def _cfg(**over):
    cfg = {
        "base_url": "https://api.example.com",  # 故意不带 /v1,测归一化
        "api_key": "sk-test",
        "model": "test-model",
        "extra_body": {},
        "provider_id": "p1",
        "provider_name": "Example",
    }
    cfg.update(over)
    return cfg


def _json_transport(payload: dict, status: int = 200) -> httpx.MockTransport:
    return httpx.MockTransport(
        lambda request: httpx.Response(status, json=payload)
    )


# ---------- tool_complete ----------

def test_tool_complete_parses_tool_calls():
    payload = {
        "choices": [{"message": {
            "content": "",
            "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": "read_file",
                             "arguments": json.dumps({"path": "a.txt"})},
            }],
        }}],
    }
    prov = OpenAICompatProvider(_cfg(), transport=_json_transport(payload))
    r = asyncio.run(prov.tool_complete([{"role": "user", "content": "hi"}], []))
    assert r["tool_calls"] == [
        {"id": "call_1", "name": "read_file", "arguments": {"path": "a.txt"}}
    ]


def test_tool_complete_returns_reasoning_content():
    """思考模式:tool_complete 要把 reasoning_content 一并带回(供多轮回传;
    DeepSeek V4 思考+工具要求带 tool_calls 的 assistant 必含思考链)。"""
    payload = {"choices": [{"message": {
        "content": "", "reasoning_content": "先想一下",
        "tool_calls": [{"id": "c1", "type": "function",
                        "function": {"name": "read_file",
                                     "arguments": json.dumps({"path": "a"})}}],
    }}]}
    prov = OpenAICompatProvider(_cfg(), transport=_json_transport(payload))
    r = asyncio.run(prov.tool_complete([{"role": "user", "content": "hi"}], []))
    assert r["reasoning_content"] == "先想一下"
    assert r["tool_calls"][0]["name"] == "read_file"


def test_reasoning_content_stripped_when_request_not_thinking():
    """reasoning_content 是思考模式产物,只有思考请求才需回传它(DeepSeek 要求)。
    非思考请求(切到普通预设或别家端点)发送前必须剥离,免得未知字段被 400。"""
    captured: dict = {}

    def handler(req):
        captured["body"] = json.loads(req.content)
        return httpx.Response(200, json={"choices": [{"message": {
            "content": "ok"}}]})

    msgs = [{"role": "assistant", "content": "", "reasoning_content": "思考链",
             "tool_calls": []}, {"role": "user", "content": "next"}]

    # 非思考(extra_body 无 thinking.enabled)→ 剥离
    p1 = OpenAICompatProvider(_cfg(extra_body={}),
                              transport=httpx.MockTransport(handler))
    asyncio.run(p1.tool_complete(msgs, []))
    assert all("reasoning_content" not in m for m in captured["body"]["messages"])

    # 思考(thinking.enabled)→ 保留
    p2 = OpenAICompatProvider(
        _cfg(extra_body={"thinking": {"type": "enabled"}}),
        transport=httpx.MockTransport(handler))
    asyncio.run(p2.tool_complete(msgs, []))
    assert any(m.get("reasoning_content") == "思考链"
               for m in captured["body"]["messages"])


def test_bad_arguments_json_degrades_to_empty_dict():
    payload = {
        "choices": [{"message": {
            "content": "",
            "tool_calls": [{
                "id": "c", "type": "function",
                "function": {"name": "x", "arguments": "{oops not json"},
            }],
        }}],
    }
    prov = OpenAICompatProvider(_cfg(), transport=_json_transport(payload))
    r = asyncio.run(prov.tool_complete([{"role": "user", "content": "hi"}], []))
    assert r["tool_calls"][0]["arguments"] == {}


def test_non_200_raises_provider_error_with_status():
    prov = OpenAICompatProvider(
        _cfg(),
        transport=httpx.MockTransport(
            lambda req: httpx.Response(500, text="boom")
        ),
    )
    with pytest.raises(ProviderError) as ei:
        asyncio.run(prov.tool_complete([{"role": "user", "content": "x"}], []))
    assert "500" in str(ei.value)


def test_missing_api_key_raises_before_http():
    prov = OpenAICompatProvider(_cfg(api_key=""))  # 没给 transport:真发请求就会炸
    with pytest.raises(ProviderError):
        asyncio.run(prov.tool_complete([{"role": "user", "content": "x"}], []))


def test_base_url_normalized_and_empty_tools_omitted():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "ok"}}]}
        )

    prov = OpenAICompatProvider(_cfg(), transport=httpx.MockTransport(handler))
    r = asyncio.run(prov.tool_complete([{"role": "user", "content": "hi"}], []))
    assert captured["url"].endswith("/v1/chat/completions")  # 自动补 /v1
    assert "tools" not in captured["body"]  # 空 tools 不传(部分上游对 [] 报错)
    assert r["content"] == "ok"


def test_tools_included_when_given():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": ""}}]}
        )

    spec = [{"type": "function",
             "function": {"name": "t", "description": "",
                          "parameters": {"type": "object", "properties": {}}}}]
    prov = OpenAICompatProvider(_cfg(), transport=httpx.MockTransport(handler))
    asyncio.run(prov.tool_complete([{"role": "user", "content": "hi"}], spec))
    assert captured["body"]["tools"] == spec


# ---------- usage 回调注入 ----------

def test_usage_callback_invoked():
    usage = {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}
    payload = {"choices": [{"message": {"content": "ok"}}], "usage": usage}
    calls: list = []
    prov = OpenAICompatProvider(
        _cfg(),
        on_usage=lambda pid, name, u: calls.append((pid, name, u)),
        transport=_json_transport(payload),
    )
    asyncio.run(prov.tool_complete([{"role": "user", "content": "hi"}], []))
    assert calls == [("p1", "Example", usage)]


def test_usage_callback_error_never_breaks_main_flow():
    payload = {"choices": [{"message": {"content": "ok"}}],
               "usage": {"total_tokens": 1}}

    def bad_hook(pid, name, u):
        raise RuntimeError("记账崩了")

    prov = OpenAICompatProvider(
        _cfg(), on_usage=bad_hook, transport=_json_transport(payload)
    )
    r = asyncio.run(prov.tool_complete([{"role": "user", "content": "hi"}], []))
    assert r["content"] == "ok"  # 回调抛错被吞,主流程不受影响


# ---------- 工厂 ----------

def test_get_provider_dispatches_openai_compat():
    p = get_provider(_cfg())
    assert isinstance(p, OpenAICompatProvider)


def test_get_provider_rejects_unknown_format():
    with pytest.raises(ValueError):
        get_provider(_cfg(format="some-unknown"))


# ---------- stream_tool_complete(流式 + 工具)----------

def _sse_bytes(*objs) -> bytes:
    out = b""
    for o in objs:
        out += b"data: " + json.dumps(o).encode("utf-8") + b"\n\n"
    return out + b"data: [DONE]\n\n"


def test_stream_tool_complete_assembles_fragmented_tool_call():
    """tool_calls 的 arguments 按增量分片到达,须按 index 拼装成完整 JSON。"""
    sse = _sse_bytes(
        {"choices": [{"delta": {"role": "assistant", "tool_calls": [
            {"index": 0, "id": "call_1", "type": "function",
             "function": {"name": "read_file", "arguments": ""}}]}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": '{"pa'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": 'th": "a.txt"}'}}]}}]},
        {"choices": [{"delta": {}}], "usage": {"total_tokens": 7}},
    )
    usage_calls: list = []
    prov = OpenAICompatProvider(
        _cfg(), on_usage=lambda pid, n, u: usage_calls.append(u),
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, content=sse)),
    )

    async def collect():
        return [e async for e in
                prov.stream_tool_complete([{"role": "user", "content": "x"}],
                                          [{"type": "function"}])]

    events = asyncio.run(collect())
    kind, final = events[-1]
    assert kind == "final"
    assert final["tool_calls"] == [
        {"id": "call_1", "name": "read_file", "arguments": {"path": "a.txt"}}]
    assert usage_calls == [{"total_tokens": 7}]


def test_stream_tool_complete_streams_text_and_reasoning():
    sse = _sse_bytes(
        {"choices": [{"delta": {"reasoning_content": "想"}}]},
        {"choices": [{"delta": {"content": "你"}}]},
        {"choices": [{"delta": {"content": "好"}}]},
    )
    prov = OpenAICompatProvider(
        _cfg(), transport=httpx.MockTransport(
            lambda req: httpx.Response(200, content=sse)),
    )

    async def collect():
        return [e async for e in
                prov.stream_tool_complete([{"role": "user", "content": "x"}],
                                          [])]

    events = asyncio.run(collect())
    assert ("reasoning", "想") in events
    assert [p for k, p in events if k == "answer"] == ["你", "好"]
    kind, final = events[-1]
    assert kind == "final"
    assert final["content"] == "你好" and final["tool_calls"] == []
    assert final["reasoning_content"] == "想"        # 思考增量需累积进 final


def test_stream_tool_complete_parallel_calls_and_bad_json():
    sse = _sse_bytes(
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "c0",
             "function": {"name": "a", "arguments": '{"k": 1}'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 1, "id": "c1",
             "function": {"name": "b", "arguments": "{oops"}}]}}]},
    )
    prov = OpenAICompatProvider(
        _cfg(), transport=httpx.MockTransport(
            lambda req: httpx.Response(200, content=sse)),
    )

    async def collect():
        return [e async for e in
                prov.stream_tool_complete([{"role": "user", "content": "x"}],
                                          [])]

    _, final = asyncio.run(collect())[-1]
    assert [c["name"] for c in final["tool_calls"]] == ["a", "b"]   # 按 index 有序
    assert final["tool_calls"][0]["arguments"] == {"k": 1}
    assert final["tool_calls"][1]["arguments"] == {}                # 坏 JSON 容错


# ---------- stream_chat(流式)----------

def test_stream_chat_yields_reasoning_and_answer_and_records_usage():
    sse = (
        b'data: {"choices":[{"delta":{"reasoning_content":"think"}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":"lo"}}],'
        b'"usage":{"total_tokens":5}}\n\n'
        b"data: [DONE]\n\n"
    )
    calls: list = []
    prov = OpenAICompatProvider(
        _cfg(),
        on_usage=lambda pid, name, u: calls.append(u),
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, content=sse)
        ),
    )

    async def collect():
        return [piece async for piece in
                prov.stream_chat([{"role": "user", "content": "hi"}])]

    out = asyncio.run(collect())
    assert ("reasoning", "think") in out
    assert [p for k, p in out if k == "answer"] == ["Hel", "lo"]
    assert calls == [{"total_tokens": 5}]
