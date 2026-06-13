// 后端基址：开发(Vite 跨端口) 或 打包(file:// 加载) → 绝对地址打后端；
// 被后端同源(http)托管(浏览器/局域网) → 空串走相对路径。
const DEV_BACKEND = "http://127.0.0.1:8756";
export const backendBase =
  import.meta.env.DEV ||
  (typeof location !== "undefined" &&
    !location.protocol.startsWith("http"))
    ? DEV_BACKEND
    : "";

export interface Health {
  status: string;
  version: string;
}

export interface WhoAmI {
  trust: "local" | "remote";
  client_host: string;
  permissions: Record<string, boolean>;
}

export interface Preset {
  label: string;
  model: string;
  extra_body: Record<string, unknown>;
  pinned?: boolean;
  description?: string;
}

export interface ProviderInfo {
  id: string;
  name: string;
  format: string;
  base_url: string;
  api_key_set: boolean;
  capability: string;
  presets: Preset[];
}

export interface ActiveSel {
  provider_id: string;
  preset_label: string;
}

export interface ProvidersState {
  providers: ProviderInfo[];
  active: ActiveSel;
}

export type ThemeMode = "dark" | "light" | "system";

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(backendBase + path, { credentials: "include" });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return (await res.json()) as T;
}

async function sendJSON<T>(
  path: string,
  method: string,
  body?: unknown
): Promise<T> {
  const res = await fetch(backendBase + path, {
    method,
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return (await res.json()) as T;
}

export const getHealth = () => getJSON<Health>("/api/health");
export const getWhoAmI = () => getJSON<WhoAmI>("/api/whoami");

export const getProviders = () => getJSON<ProvidersState>("/api/providers");
export const upsertProvider = (patch: Partial<ProviderInfo> & {
  api_key?: string;
}) => sendJSON<ProviderInfo>("/api/providers", "POST", patch);
export const deleteProvider = (id: string) =>
  sendJSON<{ ok: boolean }>(`/api/providers/${id}`, "DELETE");
export const togglePresetPin = (
  provider_id: string,
  label: string,
  pinned: boolean,
) =>
  sendJSON<ProviderInfo>(`/api/providers/${provider_id}/pin`, "POST", {
    label,
    pinned,
  });
export const setActive = (provider_id: string, preset_label: string) =>
  sendJSON<ActiveSel>("/api/active", "POST", { provider_id, preset_label });

export interface UsageRow {
  name: string;
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
}
export interface UsageState {
  rows: UsageRow[];
  totals: Omit<UsageRow, "name">;
  updated: string;
}
export const getUsage = () => getJSON<UsageState>("/api/usage");
export const resetUsage = () =>
  sendJSON<UsageState>("/api/usage/reset", "POST");

export interface ProviderTest {
  ok: boolean;
  ms?: number;
  model?: string;
  error?: string;
}
export const testProvider = (provider_id: string, preset_label: string) =>
  sendJSON<ProviderTest>("/api/providers/test", "POST", {
    provider_id,
    preset_label,
  });

export interface DiscoverResp {
  models: string[];
  errors?: string[];
}
export const discoverProviderModels = (
  base_url: string,
  opts?: { api_key?: string },
) =>
  sendJSON<DiscoverResp>("/api/providers/discover", "POST", {
    base_url,
    api_key: opts?.api_key || null,
  });

export interface LogsResp {
  text: string;
  path: string;
  size: number;
  max_total: number;
}
export const getLogs = (lines = 200) =>
  getJSON<LogsResp>("/api/logs?lines=" + lines);
export const clearLogs = () => sendJSON<LogsResp>("/api/logs/clear", "POST");

export interface GitStatusResp {
  installed: boolean;
  path?: string;
  version?: string;
  error?: string;
}
export const getGitStatus = () => getJSON<GitStatusResp>("/api/git/status");
export const installGit = (url: string, install_dir: string) =>
  sendJSON<{
    ok: boolean;
    path?: string;
    note?: string;
    error?: string;
    installer_log_tail?: string;
  }>("/api/git/install", "POST", { url, install_dir });

/** 停止统一会话的当前运行(标 cancelled + 杀正在跑的子进程)。 */
export const agentStop = (run_id: string) =>
  sendJSON<{ ok: boolean }>("/api/agent/stop", "POST", { run_id });

// ---- 组件与更新 ----
export interface ComponentsStatus {
  skills: { installed: boolean; count: number; enabled: boolean };
  git: { installed: boolean; version?: string };
  ollama: { installed: boolean; models?: number };
}
export const getComponents = () =>
  getJSON<ComponentsStatus>("/api/components");

export interface BalanceResult {
  ok: boolean;
  supported: boolean;
  total?: number;
  used?: number;
  remaining?: number;
  currency?: string;
  unit?: string;
  error?: string;
  provider_id?: string;
}
export const getProviderBalance = (provider_id: string) =>
  getJSON<BalanceResult>(`/api/providers/${provider_id}/balance`);

export interface ProxyInfo {
  enabled: boolean;
  key: string;
  host: string;
  port: number;
  models_count: number;
}
export const getProxyInfo = () => getJSON<ProxyInfo>("/api/proxy/info");
export const toggleProxy = (enabled: boolean) =>
  sendJSON<{ enabled: boolean }>("/api/proxy/toggle", "POST", { enabled });
export const regenProxyKey = () =>
  sendJSON<{ key: string }>("/api/proxy/regen-key", "POST");

export const getTheme = () => getJSON<{ theme: ThemeMode }>("/api/theme");
export const setTheme = (theme: ThemeMode) =>
  sendJSON<{ theme: ThemeMode }>("/api/theme", "POST", { theme });

export type ConfirmLevel = "all" | "risky" | "none";
export const getConfirmLevel = () =>
  getJSON<{ confirm_level: ConfirmLevel }>("/api/confirm_level");
export const setConfirmLevel = (confirm_level: ConfirmLevel) =>
  sendJSON<{ confirm_level: ConfirmLevel }>(
    "/api/confirm_level",
    "POST",
    { confirm_level },
  );

export interface SkillsStatus {
  enabled: boolean;
  cloned: boolean;
  count: number;
  skills: string[];
}
export const getSkills = () => getJSON<SkillsStatus>("/api/skills");
export const setSkills = (enabled: boolean) =>
  sendJSON<SkillsStatus>("/api/skills", "POST", { enabled });
export const updateSkills = () =>
  sendJSON<SkillsStatus>("/api/skills/update", "POST");

export const getSystemPrompt = () =>
  getJSON<{ system_prompt: string }>("/api/system_prompt");
export const setSystemPrompt = (system_prompt: string) =>
  sendJSON<{ system_prompt: string }>("/api/system_prompt", "POST", {
    system_prompt,
  });

export interface WorkspaceCfg {
  allowed_roots: string[];
  cwd: string;
  test_cmd: string;
  cwd_is_git?: boolean;
}
export const getWorkspace = () => getJSON<WorkspaceCfg>("/api/workspace");
export const saveWorkspace = (patch: Partial<WorkspaceCfg>) =>
  sendJSON<WorkspaceCfg>("/api/workspace", "POST", patch);
export const gitInitWorkspace = (path: string) =>
  sendJSON<{ initialized?: boolean; already_git?: boolean }>(
    "/api/workspace/git-init",
    "POST",
    { path }
  );
export interface AgentSessionSummary {
  id: string;
  title: string;
  updated: number;
  cwd: string;
}
export interface AgentSessionFull extends AgentSessionSummary {
  checkpoint: string | null;
  transcript: AgentEvent[];
  status: string;
  web_on?: boolean;
}
export const listAgentSessions = () =>
  getJSON<AgentSessionSummary[]>("/api/agent/sessions");
export const getAgentSession = (id: string) =>
  getJSON<AgentSessionFull>(`/api/agent/sessions/${id}`);
export const deleteAgentSession = (id: string) =>
  sendJSON<{ ok: boolean }>(`/api/agent/sessions/${id}`, "DELETE");

export const agentRollback = (run_id: string, to?: string) =>
  sendJSON<{ rolled_back_to?: string; error?: string }>(
    "/api/agent/rollback",
    "POST",
    { run_id, to }
  );

export interface TodoItem {
  id: string;
  title: string;
  status: "pending" | "in_progress" | "completed";
}

export interface AgentEvent {
  type:
    | "user"
    | "run"
    | "checkpoint"
    | "tool"
    | "result"
    | "confirm"
    | "answer"
    | "delta"      // 流式回答增量(瞬时,不入 transcript)
    | "reasoning"  // 流式思考增量(瞬时,不入 transcript)
    | "cancelled"
    | "done"
    | "error"
    | "info"
    | "todos";
  run_id?: string;
  commit?: string;
  name?: string;
  tool?: string;
  args?: Record<string, unknown>;
  call_id?: string;
  result?: unknown;
  content?: string;
  error?: string;
  items?: TodoItem[]; // type === "todos" 时携带最新清单
}

// 通用 SSE POST：逐事件回调，返回 AbortController
export function streamSSE(
  path: string,
  body: unknown,
  onEvent: (e: AgentEvent) => void,
  onClose: () => void
): AbortController {
  const ac = new AbortController();
  (async () => {
    try {
      const res = await fetch(backendBase + path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify(body),
        signal: ac.signal,
      });
      if (!res.ok || !res.body) {
        onEvent({ type: "error", error: `请求失败 ${res.status}` });
        onClose();
        return;
      }
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const parts = buf.split("\n\n");
        buf = parts.pop() ?? "";
        for (const part of parts) {
          const line = part.trim();
          if (!line.startsWith("data:")) continue;
          onEvent(JSON.parse(line.slice(5).trim()) as AgentEvent);
        }
      }
    } catch (e) {
      if ((e as Error).name !== "AbortError")
        onEvent({ type: "error", error: (e as Error).message });
    } finally {
      onClose();
    }
  })();
  return ac;
}

// ---- 联网搜索(独立配置)----
export interface SearchCfg {
  provider: string;
  api_key_set: boolean;
  max_results: number;
}
export interface SearchTestResult {
  ok: boolean;
  provider: string;
  count?: number;
  results?: { title: string; snippet: string; url: string }[];
  error?: string;
}
export const getSearchCfg = () => getJSON<SearchCfg>("/api/search");
export const saveSearchCfg = (patch: {
  provider?: string;
  api_key?: string; // ""=不改;"__clear__"=清空;其它=设新值
  max_results?: number;
}) => sendJSON<SearchCfg>("/api/search", "POST", patch);
export const testSearch = (query = "ping") =>
  sendJSON<SearchTestResult>("/api/search/test", "POST", { query });

// 原生文件夹选择器（仅 Electron 外壳；远程浏览器没有这个能力）。
export const hasNativePicker = (): boolean => {
  const w = window as unknown as { cogito?: { pickFolder?: unknown } };
  return typeof w.cogito?.pickFolder === "function";
};
export const pickFolder = async (): Promise<string> => {
  const w = window as unknown as {
    cogito?: { pickFolder?: () => Promise<string> };
  };
  return w.cogito?.pickFolder ? await w.cogito.pickFolder() : "";
};

