"""按 provider base_url 查余量。能查的就报数字 + 进度条用得上的 used/total;
不能查的(Gemini/Anthropic/Groq 这类没公开端点)就报 supported=False。

返回统一形如:
  {ok, supported, total?, used?, remaining?, currency?, unit?, raw?, error?}
remaining/total 给前端画条;raw 给前端显示原始 JSON 排错。
"""
from __future__ import annotations
from typing import Any
import httpx
from urllib.parse import urlparse


def _domain(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _client(proxy: str | None) -> httpx.Client:
    return httpx.Client(timeout=12.0, proxy=proxy, follow_redirects=True)


# ---- 各家 balance 端点 ----

def _q_deepseek(base: str, key: str, proxy: str | None) -> dict[str, Any]:
    """GET https://api.deepseek.com/user/balance → balance_infos[]
    {currency, total_balance, granted_balance, topped_up_balance}"""
    url = "https://api.deepseek.com/user/balance"
    with _client(proxy) as c:
        r = c.get(url, headers={"Authorization": f"Bearer {key}"})
    if r.status_code != 200:
        return {"ok": False, "supported": True,
                "error": f"{r.status_code} {r.text[:200]}"}
    data = r.json()
    infos = data.get("balance_infos") or []
    if not infos:
        return {"ok": False, "supported": True, "error": "balance_infos 为空",
                "raw": data}
    # 用第一个 currency(通常 CNY 或 USD)
    info = infos[0]
    total = float(info.get("total_balance") or 0)
    granted = float(info.get("granted_balance") or 0)
    topped = float(info.get("topped_up_balance") or 0)
    return {
        "ok": True, "supported": True,
        "remaining": total,
        "total": granted + topped,  # 历史总额(赠送+充值)
        "used": (granted + topped) - total,
        "currency": info.get("currency", "USD"),
        "unit": "money",
        "raw": data,
    }


def _q_openrouter(base: str, key: str, proxy: str | None) -> dict[str, Any]:
    """GET https://openrouter.ai/api/v1/auth/key →
    {data: {limit, usage, label, ...}} limit/usage 单位 USD。
    免费 key 的 limit 可能是 null 表示无固定额度 → 这种就报 used only。"""
    url = "https://openrouter.ai/api/v1/auth/key"
    with _client(proxy) as c:
        r = c.get(url, headers={"Authorization": f"Bearer {key}"})
    if r.status_code != 200:
        return {"ok": False, "supported": True,
                "error": f"{r.status_code} {r.text[:200]}"}
    data = (r.json() or {}).get("data") or {}
    limit = data.get("limit")
    usage = float(data.get("usage") or 0)
    out: dict[str, Any] = {
        "ok": True, "supported": True,
        "used": usage,
        "currency": "USD", "unit": "money", "raw": data,
    }
    if limit is not None:
        out["total"] = float(limit)
        out["remaining"] = max(0.0, float(limit) - usage)
    return out


def _q_moonshot(base: str, key: str, proxy: str | None) -> dict[str, Any]:
    """Moonshot Kimi: GET https://api.moonshot.cn/v1/users/me/balance
    → {data: {available_balance, voucher_balance, cash_balance}}"""
    url = "https://api.moonshot.cn/v1/users/me/balance"
    with _client(proxy) as c:
        r = c.get(url, headers={"Authorization": f"Bearer {key}"})
    if r.status_code != 200:
        return {"ok": False, "supported": True,
                "error": f"{r.status_code} {r.text[:200]}"}
    data = (r.json() or {}).get("data") or {}
    avail = float(data.get("available_balance") or 0)
    return {
        "ok": True, "supported": True,
        "remaining": avail,
        "currency": "CNY", "unit": "money", "raw": data,
    }


def _q_siliconflow(base: str, key: str, proxy: str | None) -> dict[str, Any]:
    """SiliconFlow: GET https://api.siliconflow.cn/v1/user/info
    → {data: {balance, totalBalance, chargeBalance}}"""
    url = "https://api.siliconflow.cn/v1/user/info"
    with _client(proxy) as c:
        r = c.get(url, headers={"Authorization": f"Bearer {key}"})
    if r.status_code != 200:
        return {"ok": False, "supported": True,
                "error": f"{r.status_code} {r.text[:200]}"}
    data = (r.json() or {}).get("data") or {}
    return {
        "ok": True, "supported": True,
        "remaining": float(data.get("balance") or 0),
        "total": float(data.get("totalBalance") or 0) or None,
        "currency": "CNY", "unit": "money", "raw": data,
    }


# host 关键字 → 查询函数。匹配是子串包含,顺序无关
_REGISTRY: list[tuple[str, Any]] = [
    ("deepseek.com", _q_deepseek),
    ("openrouter.ai", _q_openrouter),
    ("moonshot.cn", _q_moonshot),
    ("moonshot.ai", _q_moonshot),
    ("siliconflow.cn", _q_siliconflow),
]


def query_balance(base_url: str, api_key: str,
                  proxy: str | None = None) -> dict[str, Any]:
    """统一入口。不支持的 host 返 supported=False。"""
    host = _domain(base_url)
    if not host:
        return {"ok": False, "supported": False, "error": "base_url 无效"}
    if not api_key:
        return {"ok": False, "supported": False, "error": "缺 api_key"}
    for keyword, fn in _REGISTRY:
        if keyword in host:
            try:
                return fn(base_url, api_key, proxy)
            except httpx.HTTPError as e:
                return {"ok": False, "supported": True,
                        "error": f"网络错误: {type(e).__name__} {str(e)[:200]}"}
            except (ValueError, KeyError) as e:
                return {"ok": False, "supported": True,
                        "error": f"解析失败: {str(e)[:200]}"}
    return {
        "ok": False, "supported": False,
        "error": f"{host} 暂无余量查询(Gemini/OpenAI/Anthropic/Groq "
                  "等没公开 balance 端点)",
    }
