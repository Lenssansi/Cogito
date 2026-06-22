"""自动记忆提炼测试 —— 门控 / JSON 解析 / 落库 / 带假 provider 的完整 run。"""

from __future__ import annotations

import asyncio

import memory
import memory_extract


# ---------- 门控 ----------

def test_gate_signals_and_tools():
    assert memory_extract.worth_extracting("以后都用中文回复我", "", False)   # 信号词
    assert memory_extract.worth_extracting("随便看看", "", True)              # 用过工具
    assert memory_extract.worth_extracting("x" * 200, "", False)             # 较长实质输入


def test_gate_skips_chitchat():
    assert not memory_extract.worth_extracting("你好", "", False)
    assert not memory_extract.worth_extracting("谢谢啦", "", False)


# ---------- JSON 解析(容忍前言/围栏/脏输入)----------

def test_parse_clean():
    out = memory_extract.parse_candidates(
        '[{"name":"a","description":"d","type":"user","body":"b"}]')
    assert out == [{"name": "a", "description": "d", "type": "user", "body": "b"}]


def test_parse_with_preamble_and_fence():
    txt = "想了想,结果:\n```json\n[{\"name\":\"x\",\"description\":\"y\"}]\n```"
    out = memory_extract.parse_candidates(txt)
    assert out[0]["name"] == "x" and out[0]["body"] == "y"   # body 缺省=description


def test_parse_empty_and_garbage():
    assert memory_extract.parse_candidates("[]") == []
    assert memory_extract.parse_candidates("这不是 JSON") == []
    assert memory_extract.parse_candidates('[{"name":"a"}]') == []   # 缺 description → 丢


# ---------- 带假 provider 的完整流程 ----------

class _Fake:
    def __init__(self, content):
        self._c = content

    async def tool_complete(self, messages, specs):
        return {"content": self._c, "tool_calls": []}


class _Boom:
    async def tool_complete(self, messages, specs):
        raise RuntimeError("provider 炸了")


def _root(tmp_path, monkeypatch):
    monkeypatch.setenv("COGITO_HOME", str(tmp_path / "home"))
    return str(tmp_path / "proj")


def test_run_extracts_and_saves(tmp_path, monkeypatch):
    root = _root(tmp_path, monkeypatch)
    prov = _Fake('[{"name":"likes-chinese","description":"用户偏好中文交流",'
                 '"type":"user","body":"用中文回复"}]')
    res = asyncio.run(memory_extract.run(root, "以后都用中文", "好的", prov))
    assert "likes-chinese" in res["written"]
    assert "likes-chinese" in memory.load_index(root)        # 真落库了


def test_run_empty_saves_nothing(tmp_path, monkeypatch):
    root = _root(tmp_path, monkeypatch)
    res = asyncio.run(memory_extract.run(root, "随便", "随便", _Fake("[]")))
    assert res["written"] == []


def test_run_provider_error_is_safe(tmp_path, monkeypatch):
    root = _root(tmp_path, monkeypatch)
    res = asyncio.run(memory_extract.run(root, "x", "y", _Boom()))
    assert res["written"] == [] and "error" in res            # 不抛,安全降级
