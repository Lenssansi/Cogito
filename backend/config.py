"""配置加载/保存 + 多 API（providers）数据模型。

settings.json 放 data/（gitignore，含远程令牌与各 API 密钥）。
P2：单一 provider 升级为 providers[] + active 指针 + 每 API 的预设(presets)。
预设 = 标签 + 模型 id + 可选额外请求参数(extra_body)，思考/非思考、
不同模型都靠预设表达，provider 层不写任何厂商特例。
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

if getattr(sys, "frozen", False):
    # PyInstaller 打包后：exe 位于 <resources>/backend/cogito-backend.exe
    # → PROJECT_ROOT=<resources>（含 skills/）；data 写到可写的 %APPDATA%
    _EXE = Path(sys.executable).resolve()
    PROJECT_ROOT = _EXE.parent.parent
    DATA_DIR = Path(
        os.environ.get("APPDATA") or str(PROJECT_ROOT)
    ) / "Cogito"
else:
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    DATA_DIR = PROJECT_ROOT / "data"
SETTINGS_PATH = DATA_DIR / "settings.json"

# DeepSeek 开箱预设（已据官方文档 2026-04：思考靠 thinking 参数，模型 v4-flash/pro）
def _deepseek_presets() -> list[dict[str, Any]]:
    # supports_tools: false 标记该预设无法使用 function-calling
    # (DeepSeek 思考模式当前不支持工具)。agent/file 模式遇到会按规则兜底
    return [
        {"label": "V4 Flash·普通", "model": "deepseek-v4-flash",
         "extra_body": {"thinking": {"type": "disabled"}}},
        {"label": "V4 Flash·思考", "model": "deepseek-v4-flash",
         "extra_body": {"thinking": {"type": "enabled"}},
         "supports_tools": False},
        {"label": "V4 Pro·普通", "model": "deepseek-v4-pro",
         "extra_body": {"thinking": {"type": "disabled"}}},
        {"label": "V4 Pro·思考", "model": "deepseek-v4-pro",
         "extra_body": {"thinking": {"type": "enabled"}},
         "supports_tools": False},
    ]


def _default_provider() -> dict[str, Any]:
    return {
        "id": uuid.uuid4().hex[:12],
        "name": "DeepSeek",
        "format": "openai_compat",
        "base_url": "https://api.deepseek.com",
        "api_key": "",
        "capability": "推理、代码、数学较强，长文本与中文表现不错",
        "presets": _deepseek_presets(),
    }


DEFAULTS: dict[str, Any] = {
    "host": "127.0.0.1",
    "port": 8756,
    "remote_enabled": False,
    "token": "",
    "proxy_api_key": "",  # /v1/* 聚合代理 Bearer key,首次启动自动生成
    "proxy_enabled": True,
    "providers": [],
    "active": {"provider_id": "", "preset_label": ""},
    "theme": "light",  # dark | light | system —— 首次安装默认亮色
    # 高危确认档：all=每个改动都确认；risky=仅删/跑命令/回滚/动安全边界
    # 才确认（默认，新建/写/改静默执行，有 git 检查点兜底）；none=全不确认
    "confirm_level": "risky",
    "system_prompt": "",  # 全局系统提示词，对所有对话/所有 API 生效
    "skills_enabled": True,  # 写代码时注入工程 skills（仅编程 Agent）
    "workspace": {
        # 默认含本助手根目录；用户在设置页逐条增删（仅本机可改）
        "allowed_roots": [str(PROJECT_ROOT)],
        "cwd": str(PROJECT_ROOT),
        "test_cmd": "",       # 改动后自动跑的测试命令，如 pytest / npm test
    },
    "ollama": {"base_url": "http://localhost:11434", "model": "qwen2.5:3b"},
    # 联网搜索:独立于 providers,自己一套配置(用户可在 API 管理里也存
    # 同一 key,两边互不可见、互不影响)
    "search": {
        "provider": "tavily",  # tavily | off
        "api_key": "",
        "max_results": 5,
    },
}


def _ensure_token(s: dict[str, Any]) -> bool:
    dirty = False
    if not s.get("token"):
        s["token"] = secrets.token_urlsafe(24)
        dirty = True
    if not s.get("proxy_api_key"):
        s["proxy_api_key"] = "cogito-" + secrets.token_urlsafe(20)
        dirty = True
    return dirty


def get_proxy_key() -> str:
    return load_settings().get("proxy_api_key", "")


def regenerate_proxy_key() -> str:
    s = load_settings()
    s["proxy_api_key"] = "cogito-" + secrets.token_urlsafe(20)
    save_settings(s)
    return s["proxy_api_key"]


def get_proxy_enabled() -> bool:
    return bool(load_settings().get("proxy_enabled", True))


def set_proxy_enabled(v: bool) -> bool:
    s = load_settings()
    s["proxy_enabled"] = bool(v)
    save_settings(s)
    return s["proxy_enabled"]


OLLAMA_PID = "__ollama__"  # 历史值,仅用于迁移老配置时识别


_OLLAMA_CACHE: dict[str, Any] = {"ts": 0.0, "base": "", "models": []}


def _ollama_models(base_url: str) -> list[str]:
    """列指定 base_url 的 Ollama 模型;短超时 + 8s 缓存。"""
    base = (base_url or "").rstrip("/")
    now = time.time()
    if _OLLAMA_CACHE["base"] == base and now - _OLLAMA_CACHE["ts"] < 8:
        return _OLLAMA_CACHE["models"]
    ms: list[str] = []
    try:
        r = httpx.get(f"{base}/api/tags", timeout=1.5)
        if r.status_code == 200:
            ms = [m.get("name", "")
                  for m in r.json().get("models", [])
                  if m.get("name")]
    except (httpx.HTTPError, ValueError, KeyError):
        ms = []
    _OLLAMA_CACHE.update(ts=now, base=base, models=ms)
    return ms


def _default_local_provider(base_url: str = "http://localhost:11434",
                              fallback_model: str = "qwen2.5:3b"
                              ) -> dict[str, Any]:
    """首次种「本地模型」普通 provider:用本机已装 Ollama 模型做预设,
    没装则用 fallback_model 占位。用户随后可自己改 base_url/加模型。"""
    models = _ollama_models(base_url)
    if not models:
        models = [fallback_model]
    return {
        "id": uuid.uuid4().hex[:12],
        "name": "本地模型",
        "format": "openai_compat",
        "base_url": base_url,
        "api_key": "local",  # 占位,本地不验证 key
        "capability": "本机模型:免费、隐私;可改地址/加预设;适合简单/离线",
        "presets": [{"label": m, "model": m, "extra_body": {}}
                    for m in models],
    }


def _ensure_local_provider(s: dict[str, Any]) -> bool:
    """确保 providers 里有一个『本地模型』入口(原合成 __ollama__ 的接班)。"""
    has_local = any(
        ("11434" in p.get("base_url", ""))
        or p.get("name") in ("本地模型", "本地 Ollama")
        for p in s.get("providers", [])
    )
    changed = False
    if not has_local:
        oc = s.get("ollama", {})
        local = _default_local_provider(
            oc.get("base_url", "http://localhost:11434"),
            oc.get("model", "qwen2.5:3b"),
        )
        s.setdefault("providers", []).append(local)
        changed = True
    # 老配置 active 还指向 __ollama__ 的,迁到新本地 provider
    if s.get("active", {}).get("provider_id") == OLLAMA_PID:
        target = next(
            (p for p in s["providers"]
             if "11434" in p.get("base_url", "")
             or p.get("name") in ("本地模型", "本地 Ollama")),
            None,
        )
        if target:
            preset_label = s["active"].get("preset_label", "")
            # 若旧 preset_label 不在新 provider 预设里,退化到第一个
            valid_labels = {x["label"] for x in target.get("presets", [])}
            if preset_label not in valid_labels:
                preset_label = (target["presets"][0]["label"]
                                if target.get("presets") else "")
            s["active"] = {"provider_id": target["id"],
                            "preset_label": preset_label}
            changed = True
    return changed


def _migrate(s: dict[str, Any], raw: dict[str, Any]) -> bool:
    """旧单 provider → providers[]；空则种一个 DeepSeek 默认条目。
    返回是否发生了迁移（用于判断是否需要落盘）。"""
    if s.get("providers"):
        return False
    old = raw.get("provider")
    if isinstance(old, dict):
        base = old.get("base_url", "")
        if "deepseek" in base:
            presets = _deepseek_presets()
            active_label = presets[0]["label"]
        else:
            m = old.get("model") or "默认"
            presets = [{"label": m, "model": m, "extra_body": {}}]
            active_label = m
        prov = {
            "id": uuid.uuid4().hex[:12],
            "name": "DeepSeek" if "deepseek" in base
            else (base.split("//")[-1].split("/")[0] or "默认"),
            "format": old.get("format", "openai_compat"),
            "base_url": base,
            "api_key": old.get("api_key", ""),
            "capability": ("推理、代码、数学较强，长文本与中文表现不错"
                           if "deepseek" in base else ""),
            "presets": presets,
        }
        s["providers"] = [prov]
        s["active"] = {"provider_id": prov["id"],
                       "preset_label": active_label}
    else:
        prov = _default_provider()
        s["providers"] = [prov]
        s["active"] = {"provider_id": prov["id"],
                       "preset_label": prov["presets"][0]["label"]}
    s.pop("provider", None)
    return True


_BAK_PATH = SETTINGS_PATH.with_suffix(".json.bak")


def _read_raw() -> dict[str, Any] | None:
    """读 settings.json；解析失败回退 .bak；都失败返回 None
    （表示「未知/读取异常」，绝不当成空配置去重置，否则会丢 key）。"""
    for path in (SETTINGS_PATH, _BAK_PATH):
        if not path.exists():
            continue
        try:
            txt = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if not txt.strip():
            continue
        try:
            return json.loads(txt)
        except json.JSONDecodeError:
            continue  # 可能读到并发写一半，试 .bak
    return None


def load_settings() -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    file_exists = SETTINGS_PATH.exists()
    raw = _read_raw()
    read_failed = raw is None and file_exists   # 文件在但读/解析异常
    truly_first = raw is None and not file_exists
    raw = raw or {}
    s = {**DEFAULTS, **raw}

    dirty = truly_first
    if _ensure_token(s):
        dirty = True
    if _migrate(s, raw):
        dirty = True
    if _ensure_local_provider(s):
        dirty = True
    # 回填：早于 P3 迁移过的 provider 没 capability，DeepSeek 给默认值
    for p in s.get("providers", []):
        if "capability" not in p:
            p["capability"] = ""
            dirty = True
        if not p["capability"] and "deepseek" in p.get("base_url", ""):
            p["capability"] = "推理、代码、数学较强，长文本与中文表现不错"
            dirty = True
    # 工作区白名单始终至少含本助手根目录
    ws = s.setdefault("workspace", {})
    roots = ws.setdefault("allowed_roots", [])
    if str(PROJECT_ROOT) not in roots:
        roots.insert(0, str(PROJECT_ROOT))
        dirty = True
    if not ws.get("cwd"):
        ws["cwd"] = str(PROJECT_ROOT)
        dirty = True
    ws.setdefault("test_cmd", "")

    # 读取异常时绝不落盘（防止用默认值覆盖、丢 key）；
    # 否则只有真有变更才写——杜绝「每次读都写」引发的并发损坏。
    if dirty and not read_failed:
        save_settings(s)
    return s


def save_settings(s: dict[str, Any]) -> None:
    """原子写入：先写 .tmp 再 os.replace；替换前备份上一份为 .bak。
    这样并发读取永远只会看到完整的旧文件或完整的新文件。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    data = json.dumps(s, ensure_ascii=False, indent=2)
    tmp = SETTINGS_PATH.with_suffix(".json.tmp")
    tmp.write_text(data, encoding="utf-8")
    if SETTINGS_PATH.exists():
        try:
            _BAK_PATH.write_text(
                SETTINGS_PATH.read_text(encoding="utf-8"), encoding="utf-8"
            )
        except OSError:
            pass
    os.replace(tmp, SETTINGS_PATH)


# ---------- providers ----------

def _find(providers: list[dict], pid: str) -> dict | None:
    return next((p for p in providers if p["id"] == pid), None)


def mask_provider(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": p["id"],
        "name": p.get("name", ""),
        "format": p.get("format", "openai_compat"),
        "base_url": p.get("base_url", ""),
        "api_key_set": bool(p.get("api_key")),
        "capability": p.get("capability", ""),
        "presets": p.get("presets", []),
    }


def discover_models(base_url: str,
                     api_key: str | None = None,
                     proxy: str | None = None) -> dict[str, Any]:
    """探测可用模型。返回 {models: [...], errors: [...]}。
    errors 给前端看具体原因(Gemini 经常返 'User location is not supported',
    机场节点 IP 在 Google 黑名单导致;这种情况不是 key 错,是节点不行)。"""
    base = (base_url or "").rstrip("/")
    if not base:
        return {"models": [], "errors": ["base_url 为空"]}
    errors: list[str] = []
    # 不带 key 时 Ollama 优先(本机一般免认证)
    if not api_key:
        ms = _ollama_models(base)
        if ms:
            return {"models": ms, "errors": []}
    # 尝试多种 OpenAI 兼容路径:Gemini 是 .../openai/models 不是 .../v1/models
    candidates = []
    if base.endswith("/v1") or base.endswith("/openai"):
        candidates.append(base + "/models")  # 已含版本/接入点段
    else:
        candidates.append(base + "/v1/models")
        candidates.append(base + "/models")  # 兜底直接挂
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    for url in candidates:
        try:
            with httpx.Client(timeout=10.0, headers=headers, proxy=proxy,
                              follow_redirects=True) as c:
                r = c.get(url)
            if r.status_code == 200:
                data = r.json().get("data") or []
                ids = [m.get("id", "") for m in data if m.get("id")]
                # 去掉路径前缀(Gemini 给 "models/gemini-2.5-pro")
                ids = [i.split("/", 1)[-1] if i.startswith("models/")
                        else i for i in ids]
                if ids:
                    return {"models": ids, "errors": []}
                errors.append(f"{url} 200 但模型列表为空")
            else:
                # 抽错误正文最关键的一行
                msg = ""
                try:
                    body = r.json()
                    if isinstance(body, dict):
                        err = body.get("error", body)
                        if isinstance(err, dict):
                            msg = err.get("message") or err.get("status") or ""
                        elif isinstance(err, str):
                            msg = err
                except Exception:  # noqa: BLE001
                    msg = r.text[:200]
                errors.append(f"{r.status_code} {url}: {msg[:300]}")
        except httpx.HTTPError as e:
            errors.append(f"{url}: {type(e).__name__} {str(e)[:200]}")
    return {"models": [], "errors": errors}


def public_state() -> dict[str, Any]:
    s = load_settings()
    provs = [mask_provider(p) for p in s["providers"]]
    return {"providers": provs, "active": s["active"]}


def get_active_resolved() -> dict[str, Any] | None:
    """供 /api/chat：返回 {format,base_url,api_key,model,extra_body}。"""
    s = load_settings()
    act = s.get("active", {})
    prov = _find(s["providers"], act.get("provider_id", ""))
    if not prov:
        return None
    presets = prov.get("presets", [])
    preset = next(
        (x for x in presets if x["label"] == act.get("preset_label")),
        presets[0] if presets else None,
    )
    if not preset:
        return None
    return {
        "format": prov.get("format", "openai_compat"),
        "base_url": prov.get("base_url", ""),
        "api_key": prov.get("api_key", ""),
        "model": preset.get("model", ""),
        "extra_body": preset.get("extra_body", {}) or {},
        "provider_id": prov["id"],
        "provider_name": prov.get("name", ""),
        "preset_label": preset.get("label", ""),
    }


def resolve_choice(provider_id: str, preset_label: str) -> dict[str, Any] | None:
    """按指定 provider+preset 解析（供路由器选择后使用）。"""
    s = load_settings()
    prov = _find(s["providers"], provider_id)
    if not prov:
        return None
    presets = prov.get("presets", [])
    preset = next(
        (x for x in presets if x["label"] == preset_label),
        presets[0] if presets else None,
    )
    if not preset:
        return None
    return {
        "format": prov.get("format", "openai_compat"),
        "base_url": prov.get("base_url", ""),
        "api_key": prov.get("api_key", ""),
        "model": preset.get("model", ""),
        "extra_body": preset.get("extra_body", {}) or {},
        "provider_id": prov["id"],
        "provider_name": prov.get("name", ""),
        "preset_label": preset.get("label", ""),
    }


def _preset_in(prov: dict, label: str) -> dict | None:
    return next((p for p in prov.get("presets", [])
                 if p["label"] == label), None)


def resolve_tool_capable() -> tuple[dict[str, Any] | None, str]:
    """选一个支持工具调用的 provider+preset:
    优先 active(如果支持) → 否则自动兜底到支持工具的其它 provider
    (优先本地模型) → 否则返回错误字符串。返回 (resolved, error_msg)。"""
    r = get_active_resolved()
    if not r:
        return None, "未配置可用 API"
    s = load_settings()
    prov = _find(s["providers"], r.get("provider_id", ""))
    if prov:
        ps = _preset_in(prov, r.get("preset_label", ""))
        if ps is None or ps.get("supports_tools", True):
            return r, ""
    # 当前不支持工具(如思考模式) → 找候选:有任一预设 supports_tools != false
    cur_id = r.get("provider_id", "")
    candidates: list[tuple[int, str, str]] = []  # (优先级, pid, label)
    for p in s["providers"]:
        if p["id"] == cur_id:
            continue
        is_local = "11434" in p.get("base_url", "")
        for pr in p.get("presets", []):
            if pr.get("supports_tools", True):
                # 本地模型最优,其它 cloud 次之
                candidates.append((0 if is_local else 1, p["id"], pr["label"]))
                break  # 每个 provider 拿一个就够
    candidates.sort()
    for _, pid, label in candidates:
        cand = resolve_choice(pid, label)
        if cand:
            return cand, ""
    return None, (
        "没有可用的工具支持后端。请在 API 管理页添加一个支持工具调用的"
        "provider(如本机 Ollama),或换一个支持工具的当前预设。"
    )


_AGGREGATOR_HOSTS = (
    "openrouter.ai", "together.xyz", "siliconflow",
    "deepinfra.com", "huggingface.co",
    # CF AI Gateway 路径里包含 ai-gateway,以及 portkey 这类
    "portkey.ai", "anyscale.com",
)


def is_aggregator(base_url: str) -> bool:
    """通过 base_url host 判断是否聚合器(几百个模型混杂)。"""
    if not base_url:
        return False
    from urllib.parse import urlparse
    try:
        host = (urlparse(base_url).hostname or "").lower()
    except ValueError:
        return False
    return any(k in host for k in _AGGREGATOR_HOSTS)


def get_ollama() -> dict[str, Any]:
    return load_settings()["ollama"]


def set_ollama(patch: dict[str, Any]) -> dict[str, Any]:
    s = load_settings()
    cur = dict(s.get("ollama", {}))
    for k in ("base_url", "model"):
        if k in patch and patch[k]:
            cur[k] = patch[k]
    s["ollama"] = cur
    save_settings(s)
    return cur


def upsert_provider(patch: dict[str, Any]) -> dict[str, Any]:
    s = load_settings()
    providers: list[dict] = s["providers"]
    pid = patch.get("id")
    existing = _find(providers, pid) if pid else None
    if existing:
        for k in ("name", "format", "base_url", "capability", "presets"):
            if k in patch and patch[k] is not None:
                existing[k] = patch[k]
        if patch.get("api_key"):  # 空=不改
            existing["api_key"] = patch["api_key"]
        target = existing
    else:
        target = {
            "id": uuid.uuid4().hex[:12],
            "name": patch.get("name", "未命名"),
            "format": patch.get("format", "openai_compat"),
            "base_url": patch.get("base_url", ""),
            "api_key": patch.get("api_key", ""),
            "capability": patch.get("capability", ""),
            "presets": patch.get("presets", []),
        }
        providers.append(target)
        if not s["active"].get("provider_id"):
            s["active"] = {
                "provider_id": target["id"],
                "preset_label": (target["presets"][0]["label"]
                                 if target["presets"] else ""),
            }
    save_settings(s)
    return mask_provider(target)


def delete_provider(pid: str) -> bool:
    s = load_settings()
    before = len(s["providers"])
    s["providers"] = [p for p in s["providers"] if p["id"] != pid]
    if s["active"].get("provider_id") == pid:
        nxt = s["providers"][0] if s["providers"] else None
        s["active"] = {
            "provider_id": nxt["id"] if nxt else "",
            "preset_label": (nxt["presets"][0]["label"]
                             if nxt and nxt["presets"] else ""),
        }
    save_settings(s)
    return len(s["providers"]) < before


def set_active(provider_id: str, preset_label: str) -> dict[str, Any]:
    s = load_settings()
    s["active"] = {"provider_id": provider_id, "preset_label": preset_label}
    save_settings(s)
    return s["active"]


def get_system_prompt() -> str:
    return load_settings().get("system_prompt", "")


def set_system_prompt(text: str) -> str:
    s = load_settings()
    s["system_prompt"] = text or ""
    save_settings(s)
    return s["system_prompt"]


def get_workspace() -> dict[str, Any]:
    return load_settings()["workspace"]


def set_workspace(patch: dict[str, Any]) -> dict[str, Any]:
    s = load_settings()
    cur = dict(s.get("workspace", {}))
    if isinstance(patch.get("allowed_roots"), list):
        # 规范化、去重、只留存在的目录
        roots = []
        for r in patch["allowed_roots"]:
            try:
                rp = str(Path(r).resolve())
            except (OSError, RuntimeError):
                continue
            if rp not in roots:
                roots.append(rp)
        cur["allowed_roots"] = roots
    if "cwd" in patch and patch["cwd"] is not None:
        cur["cwd"] = str(patch["cwd"])
    if "test_cmd" in patch and patch["test_cmd"] is not None:
        cur["test_cmd"] = str(patch["test_cmd"])
    s["workspace"] = cur
    save_settings(s)
    return cur


def path_in_scope(target: str) -> bool:
    """target 解析后必须落在某个 allowed_root 内（防目录穿越）。"""
    roots = load_settings()["workspace"].get("allowed_roots", [])
    if not roots:
        return False
    try:
        p = Path(target).resolve()
    except (OSError, RuntimeError):
        return False
    for r in roots:
        try:
            if p == Path(r) or p.is_relative_to(Path(r)):
                return True
        except (OSError, RuntimeError, ValueError):
            continue
    return False


def get_skills_enabled() -> bool:
    return bool(load_settings().get("skills_enabled", True))


def set_skills_enabled(v: bool) -> bool:
    s = load_settings()
    s["skills_enabled"] = bool(v)
    save_settings(s)
    return s["skills_enabled"]


# 即便在 risky 档也必须用户确认：不可逆 或 改动安全边界的操作
_ALWAYS_CONFIRM = {
    "delete_path", "run_command", "git_rollback",
    "add_allowed_root",
}


def get_confirm_level() -> str:
    v = load_settings().get("confirm_level", "risky")
    return v if v in ("all", "risky", "none") else "risky"


def set_confirm_level(v: str) -> str:
    s = load_settings()
    s["confirm_level"] = v if v in ("all", "risky", "none") else "risky"
    save_settings(s)
    return s["confirm_level"]


def confirm_required(tool_name: str, high_risk: bool) -> bool:
    """按当前确认档决定该高危工具是否还要弹窗确认。
    非高危直接放行；none 全静默；all 全确认；risky 仅 _ALWAYS_CONFIRM。"""
    if not high_risk:
        return False
    lvl = get_confirm_level()
    if lvl == "none":
        return False
    if lvl == "all":
        return True
    return tool_name in _ALWAYS_CONFIRM


def get_search() -> dict[str, Any]:
    """联网搜索配置(含明文 key —— 仅供后端 search.py 用)。"""
    s = load_settings()
    cur = dict(s.get("search") or {})
    cur.setdefault("provider", "tavily")
    cur.setdefault("api_key", "")
    cur.setdefault("max_results", 5)
    return cur


def mask_search(s: dict[str, Any] | None = None) -> dict[str, Any]:
    """前端展示用:不暴露明文 key,只告知是否已设。"""
    cur = s or get_search()
    return {
        "provider": cur.get("provider", "tavily"),
        "api_key_set": bool(cur.get("api_key")),
        "max_results": int(cur.get("max_results") or 5),
    }


def set_search(patch: dict[str, Any]) -> dict[str, Any]:
    """更新搜索配置。api_key 空字符串=不改,显式设 "__clear__" 才清空。"""
    s = load_settings()
    cur = dict(s.get("search") or {})
    if "provider" in patch and patch["provider"]:
        cur["provider"] = str(patch["provider"]).strip()
    if "api_key" in patch and patch["api_key"] is not None:
        ak = str(patch["api_key"])
        if ak == "__clear__":
            cur["api_key"] = ""
        elif ak.strip():
            cur["api_key"] = ak.strip()
    if "max_results" in patch and patch["max_results"] is not None:
        try:
            cur["max_results"] = max(1, min(10, int(patch["max_results"])))
        except (TypeError, ValueError):
            pass
    s["search"] = cur
    save_settings(s)
    return mask_search(cur)


def get_theme() -> str:
    return load_settings().get("theme", "dark")


def set_theme(theme: str) -> str:
    s = load_settings()
    s["theme"] = theme if theme in ("dark", "light", "system") else "dark"
    save_settings(s)
    return s["theme"]
