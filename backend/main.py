"""Cogito 后端入口（P0 骨架）。

唯一核心服务：同时服务本机 Electron 外壳与浏览器（本机/局域网/ZeroTier）。
P3：本地小模型「大脑」(路由/直答/摘要)。
P4：编程 Agent——/api/agent/start|respond|rollback(SSE 逐步工具循环，
高危确认暂停)、/api/workspace(授权白名单/工作目录/测试命令)，
作用域护栏 + git 检查点/回滚 + 改后测试。
"""

from __future__ import annotations

import json
import os
import re
import secrets
import sys
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator
from urllib.parse import urlparse

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from config import (
    PROJECT_ROOT,
    delete_provider,
    get_active_resolved,
    get_confirm_level,
    get_system_prompt,
    get_theme,
    load_settings,
    public_state,
    get_skills_enabled,
    get_workspace,
    set_active,
    set_confirm_level,
    set_skills_enabled,
    set_system_prompt,
    set_theme,
    set_workspace,
    upsert_provider,
)
from skills_loader import status as skills_status
from agents import (
    agent_rollback,
    agent_stop,
    agent_stream_continue,
    agent_stream_respond,
    agent_stream_start,
)
from local_models import ollama_status
from search import test_query as _search_test
from llm import build_provider
from cogito_engine.providers import ProviderError
from security import Caller, get_caller, require_permission
from store import delete_agent, get_agent, list_agent

APP_VERSION = "0.2.0-p2"

# 日志一次性初始化:5MB×2 滚动到 data/logs/cogito.log;接管 uvicorn
import applog
applog.setup_logging()

app = FastAPI(title="Cogito", version=APP_VERSION)

# 开发期前端跑在 Vite(端口运行时动态分配),与后端(8756)跨端口,需放行本机源。
# 放行任意 loopback 源(127.0.0.1 / localhost 任意端口)——后端只绑本机,真正的
# 远程/跨站由下方 CSRF + 信任分级(security.py)挡;CORS 这里只让本体能读响应。
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^http://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ===== CSRF 防护 =====
# 后端绑 127.0.0.1，任何本机浏览器里的恶意网页都能 fetch 本接口。
# CORS 只挡「读响应」，挡不住「请求被执行」——所以一个 evil.com 页面能
# 静默 POST /api/git/install 等高危接口造成 RCE。这里在中间件层把关:
# 状态变更请求(POST/PUT/PATCH/DELETE)必须来自可信来源,否则 403。
#
# 放行判定(任一成立):
#  1. 带 Electron 外壳注入的 X-Cogito-Shell 令牌(app 自身请求,启动时由
#     Electron 主进程通过 onBeforeSendHeaders 注入,网页伪造不了)
#  2. Origin 与本服务同源(Origin 的 host:port == Host 头)
#  3. Origin 是本机 loopback(127.0.0.1 / localhost 任意端口,含运行时动态分配的 Vite)
#  4. 无 Origin 且非浏览器跨站(curl/Cline 等原生客户端,本就不是 CSRF 媒介)
_SHELL_TOKEN = os.environ.get("COGITO_SHELL_TOKEN", "")
_CSRF_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _csrf_ok(request: Request) -> bool:
    # 1) Electron 外壳令牌
    if _SHELL_TOKEN:
        tok = request.headers.get("X-Cogito-Shell", "")
        if tok and secrets.compare_digest(tok, _SHELL_TOKEN):
            return True
    origin = request.headers.get("origin")
    if origin:
        try:
            o = urlparse(origin)
        except ValueError:
            return False
        # 本机 loopback 源(含运行时动态端口的 Vite)= 可信本体/开发源
        if (o.hostname or "").lower() in _LOOPBACK_HOSTS:
            return True
        o_netloc = (o.netloc or "").lower()
        host = (request.headers.get("host") or "").lower()
        # 同源:Origin 的 host:port 与本服务 Host 一致(远程 LAN IP 也适用)
        if o_netloc and host and o_netloc == host:
            return True
        return False  # 有 Origin 但跨站 → 拒
    # 无 Origin:浏览器对跨源写请求一定带 Origin;没有 = 原生客户端。
    # 再用 Sec-Fetch-Site 兜底:浏览器强制设置,JS 无法伪造。
    sfs = (request.headers.get("sec-fetch-site") or "").lower()
    if sfs == "cross-site":
        return False
    return True


@app.middleware("http")
async def csrf_guard(request: Request, call_next):
    path = request.url.path
    if (path.startswith("/api/")
            and request.method.upper() not in _CSRF_SAFE_METHODS
            and not _csrf_ok(request)):
        return JSONResponse(
            status_code=403,
            content={"detail": "CSRF 校验失败:请求来源不可信。"
                     "请通过 Cogito 本体或同源页面访问。"},
        )
    return await call_next(request)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": APP_VERSION}


# ---------- 聚合 API 代理 /v1/* (OpenAI 兼容) ----------

import proxy_v1  # noqa: E402


@app.get("/v1/models")
async def v1_models(request: Request) -> dict:
    proxy_v1._check_auth(request)
    return {"object": "list", "data": proxy_v1.list_models_data()}


@app.post("/v1/chat/completions")
async def v1_chat_completions(request: Request):
    return await proxy_v1.proxy_chat_completions(request)


@app.get("/api/proxy/info")
def proxy_info(caller: Caller = Depends(get_caller)) -> dict:  # noqa: ARG001
    """供设置页显示代理 base_url、key、开关状态。"""
    from config import get_proxy_enabled, get_proxy_key
    s = load_settings()
    return {
        "enabled": get_proxy_enabled(),
        "key": get_proxy_key(),
        "host": s.get("host", "127.0.0.1"),
        "port": s.get("port", 8756),
        "models_count": len(proxy_v1.list_models_data()),
    }


class ProxyToggle(BaseModel):
    enabled: bool


@app.post("/api/proxy/toggle")
def proxy_toggle(
    body: ProxyToggle,
    caller: Caller = Depends(require_permission("settings")),  # noqa: ARG001
) -> dict:
    from config import set_proxy_enabled
    return {"enabled": set_proxy_enabled(body.enabled)}


@app.post("/api/proxy/regen-key")
def proxy_regen(
    caller: Caller = Depends(require_permission("settings")),  # noqa: ARG001
) -> dict:
    from config import regenerate_proxy_key
    return {"key": regenerate_proxy_key()}


@app.get("/api/whoami")
def whoami(caller: Caller = Depends(get_caller)) -> dict[str, object]:
    """前端据此自适应隐藏/禁用远程不可用的功能（仅体验，后端才是真门禁）。"""
    return {
        "trust": caller.trust,
        "client_host": caller.client_host,
        "permissions": caller.permissions,
    }


class PresetModel(BaseModel):
    label: str
    model: str
    extra_body: dict = Field(default_factory=dict)
    pinned: bool = False  # provider 内置顶,排前面 + 自动路由仅用置顶
    description: str = ""  # 该预设的用途说明(用户输入)


class ProviderPatch(BaseModel):
    id: str | None = None  # 有=改，无=新建
    name: str | None = None
    format: str | None = None
    base_url: str | None = None
    api_key: str | None = None  # 留空=不改，保留原 key
    capability: str | None = None  # 擅长描述，供本地模型路由
    presets: list[PresetModel] | None = None


class ActivePatch(BaseModel):
    provider_id: str
    preset_label: str


class ProviderTestReq(BaseModel):
    provider_id: str
    preset_label: str = ""


class ProviderDiscoverReq(BaseModel):
    base_url: str
    api_key: str | None = None  # Gemini/OpenAI 这类需要 Bearer 才返模型列表


class ThemePatch(BaseModel):
    theme: str


class ConfirmLevelPatch(BaseModel):
    confirm_level: str


class SystemPromptPatch(BaseModel):
    system_prompt: str


class SkillsPatch(BaseModel):
    enabled: bool


class WorkspacePatch(BaseModel):
    allowed_roots: list[str] | None = None
    cwd: str | None = None
    test_cmd: str | None = None


class AgentStart(BaseModel):
    task: str
    web: bool = True  # 是否允许 Agent 调 web_search 工具


class AgentRespond(BaseModel):
    run_id: str
    approve: bool
    edited_args: dict | None = None


class AgentContinue(BaseModel):
    run_id: str
    task: str
    web: bool = True


class AgentRollback(BaseModel):
    run_id: str
    to: str | None = None  # 回滚目标检查点 commit;不传=回到最早的点


class AgentStop(BaseModel):
    run_id: str


@app.get("/api/providers")
def list_providers(caller: Caller = Depends(get_caller)) -> dict:  # noqa: ARG001
    # 脱敏（无明文 key）；列表+预设+当前选择，远程也要据此渲染下拉
    return public_state()


@app.post("/api/providers")
def write_provider(
    patch: ProviderPatch,
    caller: Caller = Depends(require_permission("api_manage")),  # noqa: ARG001
) -> dict:
    return upsert_provider(patch.model_dump(exclude_none=True))


class PresetPinPatch(BaseModel):
    label: str
    pinned: bool


@app.post("/api/providers/{pid}/pin")
def toggle_preset_pin(
    pid: str,
    body: PresetPinPatch,
    caller: Caller = Depends(require_permission("api_manage")),  # noqa: ARG001
) -> dict:
    """翻转某 provider 某 preset 的 pinned 标志。供下拉里的 ★ 用。"""
    from config import _find, load_settings, mask_provider, save_settings
    s = load_settings()
    prov = _find(s["providers"], pid)
    if not prov:
        raise HTTPException(status_code=404, detail="provider 不存在")
    for pr in prov.get("presets", []):
        if pr.get("label") == body.label:
            pr["pinned"] = bool(body.pinned)
            save_settings(s)
            return mask_provider(prov)
    raise HTTPException(status_code=404, detail=f"preset '{body.label}' 不存在")


@app.delete("/api/providers/{pid}")
def remove_provider(
    pid: str,
    caller: Caller = Depends(require_permission("api_manage")),  # noqa: ARG001
) -> dict:
    return {"ok": delete_provider(pid)}


@app.post("/api/active")
def post_active(
    patch: ActivePatch,
    # 仅切换当前 API/预设，不碰密钥 → api_switch，远程也允许
    caller: Caller = Depends(require_permission("api_switch")),  # noqa: ARG001
) -> dict:
    return set_active(patch.provider_id, patch.preset_label)


@app.post("/api/providers/discover")
def discover_provider_models(
    req: ProviderDiscoverReq,
    caller: Caller = Depends(get_caller),
) -> dict:
    """给前端「自动发现模型」按钮:探测 base_url 的可用模型列表。
    仅本机:本端点会对任意 base_url 发起请求(SSRF 面),远程不开放。"""
    _local_only(caller)
    from config import discover_models
    result = discover_models(req.base_url, req.api_key, None)
    # 兼容:result 现在是 {models, errors};旧版只返 list
    if isinstance(result, dict):
        return {"models": result.get("models", []),
                "errors": result.get("errors", [])}
    return {"models": result, "errors": []}


@app.post("/api/providers/test")
async def test_provider(
    req: ProviderTestReq,
    caller: Caller = Depends(require_permission("api_switch")),  # noqa: ARG001
) -> dict:
    """用最小请求（max_tokens=1）实测该 API+预设能否连通。
    不写真实业务、不计入对话历史；返回 ok/耗时/错误。"""
    import time

    from config import resolve_choice

    resolved = resolve_choice(req.provider_id, req.preset_label)
    if not resolved:
        return {"ok": False, "error": "未找到该 API/预设"}
    if not resolved.get("api_key"):
        return {"ok": False, "error": "未配置 API key"}
    prov = build_provider(resolved)
    t0 = time.perf_counter()
    try:
        r = await prov.tool_complete(
            [{"role": "user", "content": "ping"}], []
        )
    except ProviderError as e:
        return {"ok": False, "error": str(e)[:300],
                "model": resolved.get("model", "")}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:300],
                "model": resolved.get("model", "")}
    ms = int((time.perf_counter() - t0) * 1000)
    _ = r
    return {"ok": True, "ms": ms, "model": resolved.get("model", "")}


@app.get("/api/theme")
def read_theme(caller: Caller = Depends(get_caller)) -> dict:  # noqa: ARG001
    return {"theme": get_theme()}


@app.post("/api/theme")
def write_theme(
    patch: ThemePatch,
    caller: Caller = Depends(require_permission("settings")),  # noqa: ARG001
) -> dict:
    return {"theme": set_theme(patch.theme)}


@app.get("/api/confirm_level")
def read_confirm_level(
    caller: Caller = Depends(get_caller),  # noqa: ARG001
) -> dict:
    return {"confirm_level": get_confirm_level()}


@app.post("/api/confirm_level")
def write_confirm_level(
    patch: ConfirmLevelPatch,
    caller: Caller = Depends(require_permission("settings")),  # noqa: ARG001
) -> dict:
    return {"confirm_level": set_confirm_level(patch.confirm_level)}


@app.get("/api/usage")
def read_usage(caller: Caller = Depends(get_caller)) -> dict:  # noqa: ARG001
    import usage
    return usage.get_usage()


# ---------- 联网搜索配置(独立于 providers)----------

class SearchPatch(BaseModel):
    provider: str | None = None
    api_key: str | None = None  # ""=不改;"__clear__"=清空
    max_results: int | None = None


class SearchTestReq(BaseModel):
    query: str = "ping"


@app.get("/api/search")
def search_read(caller: Caller = Depends(get_caller)) -> dict:
    """读联网搜索配置(脱敏,不暴露 api_key 明文)。仅本机:含 key 状态。"""
    _local_only(caller)
    from config import mask_search
    return mask_search()


@app.post("/api/search")
def search_write(
    body: SearchPatch,
    caller: Caller = Depends(require_permission("settings")),
) -> dict:
    _local_only(caller)
    from config import set_search
    return set_search(body.model_dump(exclude_none=True))


@app.post("/api/search/test")
def search_test(
    body: SearchTestReq,
    caller: Caller = Depends(get_caller),
) -> dict:
    """搜索配置自检 —— 实发一次查询,返回结果或具体错误。"""
    _local_only(caller)
    return _search_test(body.query)


# ---------- Git 检测 + 安装引导 ----------

class GitInstallReq(BaseModel):
    url: str
    install_dir: str = ""


@app.get("/api/providers/{pid}/balance")
def provider_balance(
    pid: str,
    caller: Caller = Depends(get_caller),
) -> dict:
    """查 provider 余量。已知支持:DeepSeek / OpenRouter / Moonshot /
    SiliconFlow。不支持的 host 返 {supported: False}。仅本机:用到 api_key。"""
    _local_only(caller)
    from config import _find, load_settings
    import balance as _bal
    s = load_settings()
    prov = _find(s["providers"], pid)
    if not prov:
        raise HTTPException(status_code=404, detail="provider 不存在")
    api_key = prov.get("api_key") or ""
    base_url = prov.get("base_url") or ""
    out = _bal.query_balance(base_url, api_key, None)
    out["provider_id"] = pid
    return out


@app.get("/api/components")
async def components_status(
    caller: Caller = Depends(get_caller),
) -> dict:
    """统一返回可更新外部组件状态,供设置页「组件与更新」面板渲染。
    仅本机(涉及 git/ollama 等本机组件)。"""
    _local_only(caller)
    import shutil
    import subprocess

    # 工程 skills
    sk = skills_status(get_skills_enabled())
    skills_info = {
        "installed": bool(sk.get("cloned")),
        "count": sk.get("count", 0),
        "enabled": bool(sk.get("enabled")),
    }

    # Git(系统软件,只读状态)
    gp = shutil.which("git")
    git_info: dict = {"installed": bool(gp)}
    if gp:
        try:
            r = subprocess.run([gp, "--version"], capture_output=True,
                               text=True, timeout=5, errors="replace")
            git_info["version"] = (r.stdout or "").strip()
        except Exception:  # noqa: BLE001
            pass

    # Ollama(系统软件,只读状态)
    ollama_info: dict = {"installed": False}
    try:
        st = await ollama_status()
        ollama_info = {
            "installed": bool(st.get("reachable")),
            "models": len(st.get("models", []) or []),
        }
    except Exception:  # noqa: BLE001
        pass

    return {
        "skills": skills_info,
        "git": git_info,
        "ollama": ollama_info,
    }


@app.get("/api/git/status")
def git_status(caller: Caller = Depends(get_caller)) -> dict:  # noqa: ARG001
    import shutil
    import subprocess
    p = shutil.which("git")
    if not p:
        return {"installed": False}
    try:
        r = subprocess.run([p, "--version"], capture_output=True,
                            text=True, timeout=5, errors="replace")
        return {"installed": True, "path": p,
                "version": (r.stdout or "").strip()}
    except Exception as e:  # noqa: BLE001
        return {"installed": False, "error": str(e)}


@app.post("/api/git/install")
async def git_install(
    req: GitInstallReq,
    caller: Caller = Depends(require_permission("settings")),
) -> dict:
    _local_only(caller)
    url = req.url.strip()
    if not url:
        return {"ok": False, "error": "缺少下载链接"}
    # 只允许 https —— 安装包要落地执行,绝不能走可被中间人篡改的 http
    if not url.lower().startswith("https://"):
        return {"ok": False, "error": "下载链接必须是 https://(不接受 http)"}
    import shutil
    import subprocess

    tmp_dir = PROJECT_ROOT / "data" / "tmp"
    try:
        tmp_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return {"ok": False, "error": f"无法建临时目录:{e}"}
    fn = (url.rsplit("/", 1)[-1].split("?")[0]
          or "git-installer.exe")
    # 文件名只留安全字符,杜绝路径穿越(如 ..\\..\\evil.exe)
    fn = re.sub(r"[^A-Za-z0-9._-]", "_", fn) or "git-installer.exe"
    target = tmp_dir / fn
    try:
        import httpx
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(600.0, connect=30.0),
            follow_redirects=True,
        ) as c:
            async with c.stream("GET", url) as resp:
                if resp.status_code != 200:
                    return {"ok": False,
                            "error": f"下载失败 HTTP {resp.status_code}"}
                with open(target, "wb") as f:
                    async for chunk in resp.aiter_bytes(256 * 1024):
                        f.write(chunk)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"下载失败:{e}"}

    # Inno Setup 静默参数
    args = [str(target), "/VERYSILENT", "/SUPPRESSMSGBOXES",
            "/NORESTART", "/NOCANCEL", "/SP-"]
    if req.install_dir.strip():
        args.append(f"/DIR={req.install_dir.strip()}")
    try:
        r = subprocess.run(args, capture_output=True, text=True,
                            timeout=900, errors="replace")
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "安装超时(>15min)"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"安装失败:{e}"}

    # 安装后:既看 PATH,也补查 install_dir/cmd 与默认路径
    candidate_paths = []
    if req.install_dir.strip():
        candidate_paths.append(
            str(Path(req.install_dir) / "cmd" / "git.exe")
        )
    candidate_paths += [
        r"C:\Program Files\Git\cmd\git.exe",
        r"C:\Program Files (x86)\Git\cmd\git.exe",
    ]
    found = shutil.which("git") or next(
        (p for p in candidate_paths if Path(p).is_file()), None
    )
    return {
        "ok": bool(found),
        "path": found or "",
        "note": ("Git 已装,但当前后端进程 PATH 还没刷新——重启 Cogito "
                  "应能识别。"
                  if (found and not shutil.which("git")) else ""),
        "installer_log_tail": (r.stdout or "")[-800:],
    }


@app.get("/api/logs")
def read_logs(
    lines: int = 200,
    caller: Caller = Depends(get_caller),  # noqa: ARG001
) -> dict:
    return {"text": applog.get_tail(lines), **applog.stats()}


@app.post("/api/logs/clear")
def clear_logs(
    caller: Caller = Depends(require_permission("settings")),  # noqa: ARG001
) -> dict:
    return {"ok": applog.clear_log(), **applog.stats()}


@app.post("/api/usage/reset")
def clear_usage(
    caller: Caller = Depends(require_permission("settings")),  # noqa: ARG001
) -> dict:
    import usage
    return usage.reset_usage()


@app.get("/api/system_prompt")
def read_system_prompt(
    caller: Caller = Depends(get_caller),  # noqa: ARG001
) -> dict:
    return {"system_prompt": get_system_prompt()}


@app.post("/api/system_prompt")
def write_system_prompt(
    patch: SystemPromptPatch,
    caller: Caller = Depends(require_permission("settings")),  # noqa: ARG001
) -> dict:
    return {"system_prompt": set_system_prompt(patch.system_prompt)}


_FROZEN = getattr(sys, "frozen", False)


def _local_only(caller: Caller) -> None:
    if caller.trust != "local":
        raise HTTPException(status_code=403, detail="该功能仅本机可用")


@app.get("/api/skills")
def read_skills(caller: Caller = Depends(get_caller)) -> dict:  # noqa: ARG001
    return skills_status(get_skills_enabled())


@app.post("/api/skills")
def write_skills(
    patch: SkillsPatch,
    caller: Caller = Depends(require_permission("settings")),  # noqa: ARG001
) -> dict:
    return skills_status(set_skills_enabled(patch.enabled))


@app.post("/api/skills/update")
def update_skills(
    caller: Caller = Depends(require_permission("settings")),  # noqa: ARG001
) -> dict:
    import shutil
    import subprocess
    dst = PROJECT_ROOT / "skills"
    tmp = PROJECT_ROOT / "skills__new"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    r = subprocess.run(
        ["git", "clone", "--depth", "1",
         "https://github.com/mattpocock/skills", str(tmp)],
        capture_output=True, text=True, timeout=120,
    )
    if r.returncode != 0:
        raise HTTPException(status_code=400,
                            detail=f"克隆失败：{r.stderr[:300]}")
    shutil.rmtree(tmp / ".git", ignore_errors=True)
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    tmp.rename(dst)
    return skills_status(get_skills_enabled())


def _ws_git(ws: dict) -> dict:
    cwd = ws.get("cwd", "")
    return {**ws, "cwd_is_git": bool(
        cwd and os.path.isdir(os.path.join(cwd, ".git"))
    )}


@app.get("/api/workspace")
def read_workspace(caller: Caller = Depends(get_caller)) -> dict:  # noqa: ARG001
    return _ws_git(get_workspace())


@app.post("/api/workspace")
def write_workspace(
    patch: WorkspacePatch,
    caller: Caller = Depends(require_permission("settings")),  # noqa: ARG001
) -> dict:
    return _ws_git(set_workspace(patch.model_dump(exclude_none=True)))


@app.post("/api/workspace/git-init")
def workspace_git_init(
    body: dict,
    caller: Caller = Depends(require_permission("settings")),  # noqa: ARG001
) -> dict:
    from cogito_engine.tools import git_init, ToolError
    try:
        return git_init(body.get("path", ""))
    except ToolError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/agent/start")
async def agent_start(
    body: AgentStart,
    caller: Caller = Depends(require_permission("agent")),  # noqa: ARG001
) -> StreamingResponse:
    return StreamingResponse(
        agent_stream_start(body.task, web=body.web),
        media_type="text/event-stream",
    )


@app.post("/api/agent/respond")
async def agent_respond(
    body: AgentRespond,
    caller: Caller = Depends(require_permission("agent")),  # noqa: ARG001
) -> StreamingResponse:
    return StreamingResponse(
        agent_stream_respond(body.run_id, body.approve, body.edited_args),
        media_type="text/event-stream",
    )


@app.post("/api/agent/continue")
async def agent_continue(
    body: AgentContinue,
    caller: Caller = Depends(require_permission("agent")),  # noqa: ARG001
) -> StreamingResponse:
    return StreamingResponse(
        agent_stream_continue(body.run_id, body.task, web=body.web),
        media_type="text/event-stream",
    )


@app.get("/api/agent/sessions")
def agent_sessions(
    caller: Caller = Depends(require_permission("agent")),  # noqa: ARG001
) -> list:
    return list_agent()


@app.get("/api/agent/sessions/{sid}")
def agent_session_get(
    sid: str,
    caller: Caller = Depends(require_permission("agent")),  # noqa: ARG001
) -> dict:
    s = get_agent(sid)
    if s is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return s


@app.delete("/api/agent/sessions/{sid}")
def agent_session_del(
    sid: str,
    caller: Caller = Depends(require_permission("agent")),  # noqa: ARG001
) -> dict:
    return {"ok": delete_agent(sid)}


@app.post("/api/agent/rollback")
def agent_rollback_ep(
    body: AgentRollback,
    caller: Caller = Depends(require_permission("agent")),  # noqa: ARG001
) -> dict:
    return agent_rollback(body.run_id, body.to)


@app.post("/api/agent/stop")
def agent_stop_ep(
    body: AgentStop,
    caller: Caller = Depends(require_permission("agent")),  # noqa: ARG001
) -> dict:
    """停止会话:标 cancelled + 强杀正在跑的子进程(防 run_command 阻塞
    到超时把会话卡死)。"""
    return {"ok": agent_stop(body.run_id)}


# 生产/浏览器访问：若前端已构建（app/dist），由后端直接托管。
_DIST = PROJECT_ROOT / "app" / "dist"
if _DIST.exists():
    app.mount(
        "/assets", StaticFiles(directory=_DIST / "assets"), name="assets"
    )

    @app.get("/{full_path:path}")
    def spa(full_path: str) -> FileResponse:
        # SPA 兜底绝不吞 /api/*：未匹配的 api 路径一律 404，
        # 否则前端会拿到 index.html 而不是 JSON。
        if full_path.startswith("api/") or full_path == "api":
            raise HTTPException(status_code=404, detail="未知接口")
        return FileResponse(_DIST / "index.html")


def main() -> None:
    settings = load_settings()
    host = settings["host"]
    port = int(settings["port"])
    print(f"[Cogito] backend on http://{host}:{port}  (remote_enabled="
          f"{settings['remote_enabled']})")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
