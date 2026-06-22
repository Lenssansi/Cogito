"""CSRF 来源判定回归:动态端口的本机源放行,远程源仍拒。

背景:Vite 端口改成运行时动态分配后,曾写死 5173 的 CORS/CSRF 白名单失效,
导致渲染进程被拦 → "后端未连接 / 设置不可改"。修复为放行任意 loopback 源。
"""

from __future__ import annotations

from types import SimpleNamespace

import main


class _H:
    """大小写不敏感的 headers.get(),模拟 Starlette Headers。"""

    def __init__(self, d: dict):
        self._d = {k.lower(): v for k, v in d.items()}

    def get(self, k: str, default=None):
        return self._d.get(k.lower(), default)


def _req(**headers):
    return SimpleNamespace(headers=_H(headers))


def test_csrf_allows_dynamic_loopback_origin():
    # 运行时动态分配的 Vite 端口(非 5173)也应放行
    assert main._csrf_ok(_req(origin="http://127.0.0.1:59999",
                              host="127.0.0.1:8756"))
    assert main._csrf_ok(_req(origin="http://localhost:48123",
                              host="127.0.0.1:8756"))


def test_csrf_same_origin_ok():
    assert main._csrf_ok(_req(origin="http://127.0.0.1:8756",
                              host="127.0.0.1:8756"))


def test_csrf_rejects_remote_origin():
    # 远程跨站源仍必须拒(无令牌、非本机、非同源)——安全边界不能因修复被破坏
    assert not main._csrf_ok(_req(origin="http://evil.com",
                                  host="127.0.0.1:8756"))


def test_cors_allows_null_and_dynamic_loopback():
    """CORS:打包版 file://(Origin=null)与开发版动态端口 loopback 都要放行,
    否则装好的 app / 开发窗口会连不上后端。"""
    from fastapi.testclient import TestClient
    client = TestClient(main.app)
    r = client.get("/api/health", headers={"Origin": "null"})
    assert r.headers.get("access-control-allow-origin") == "null"
    r2 = client.get("/api/health", headers={"Origin": "http://127.0.0.1:54321"})
    assert r2.headers.get("access-control-allow-origin") == "http://127.0.0.1:54321"
