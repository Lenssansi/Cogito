import { useEffect, useState } from "react";
import ComponentsPanel from "../components/ComponentsPanel";
import SearchPanel from "../components/SearchPanel";
import {
  getSystemPrompt,
  getSkills,
  getWorkspace,
  hasNativePicker,
  pickFolder,
  getLogs,
  clearLogs,
  getGitStatus,
  installGit,
  getProxyInfo,
  toggleProxy,
  regenProxyKey,
  type LogsResp,
  type GitStatusResp,
  type ProxyInfo,
  gitInitWorkspace,
  saveWorkspace,
  setSkills,
  setSystemPrompt as apiSetSysPrompt,
  setTheme as apiSetTheme,
  getConfirmLevel,
  setConfirmLevel as apiSetConfirmLevel,
  type ConfirmLevel,
  type ThemeMode,
  type SkillsStatus,
  type WhoAmI,
  type WorkspaceCfg,
} from "../api";

const THEMES: { v: ThemeMode; label: string }[] = [
  { v: "dark", label: "暗色" },
  { v: "light", label: "亮色" },
  { v: "system", label: "跟随系统" },
];

const CONFIRMS: { v: ConfirmLevel; label: string; hint: string }[] = [
  { v: "all", label: "每个都确认", hint: "任何改动/命令前都弹窗" },
  {
    v: "risky",
    label: "仅危险操作（推荐）",
    hint: "新建/写/改静默执行；删除、跑命令、Git 回滚、改安全边界才确认",
  },
  { v: "none", label: "全不确认", hint: "所有操作直接执行，谨慎使用" },
];

export default function SettingsPage({
  who,
  theme,
  onTheme,
}: {
  who: WhoAmI | null;
  theme: ThemeMode;
  onTheme: (t: ThemeMode) => void;
}) {
  const canSettings = who ? who.permissions.settings !== false : true;
  const [msg, setMsg] = useState("");
  const [sys, setSys] = useState("");
  const [sysMsg, setSysMsg] = useState("");
  const [confirmLv, setConfirmLv] = useState<ConfirmLevel>("risky");
  const [cfMsg, setCfMsg] = useState("");

  const [wsc, setWsc] = useState<WorkspaceCfg | null>(null);
  const [newRoot, setNewRoot] = useState("");
  const [wsMsg, setWsMsg] = useState("");

  const [sk, setSk] = useState<SkillsStatus | null>(null);
  const [skMsg, setSkMsg] = useState("");

  const [logs, setLogs] = useState<LogsResp | null>(null);
  const [logsMsg, setLogsMsg] = useState("");
  const [fullLogs, setFullLogs] = useState<string | null>(null);
  const [loadingFull, setLoadingFull] = useState(false);
  const [gitSt, setGitSt] = useState<GitStatusResp | null>(null);
  // 用户可改:留两个常见预填
  const [gitUrl, setGitUrl] = useState(
    "https://github.com/git-for-windows/git/releases/latest"
  );
  const [gitDir, setGitDir] = useState("");
  const [gitMsg, setGitMsg] = useState("");
  const [installing, setInstalling] = useState(false);
  const [proxy, setProxy] = useState<ProxyInfo | null>(null);
  const [proxyMsg, setProxyMsg] = useState("");

  useEffect(() => {
    getSystemPrompt()
      .then((r) => setSys(r.system_prompt))
      .catch(() => void 0);
    getConfirmLevel()
      .then((r) => setConfirmLv(r.confirm_level))
      .catch(() => void 0);
    getGitStatus()
      .then(setGitSt)
      .catch(() => void 0);
    getProxyInfo()
      .then(setProxy)
      .catch(() => void 0);
    getWorkspace()
      .then(setWsc)
      .catch(() => void 0);
    getSkills()
      .then(setSk)
      .catch(() => void 0);
  }, []);

  async function saveWs(next: Partial<WorkspaceCfg>) {
    setWsMsg("");
    try {
      setWsc(await saveWorkspace(next));
      setWsMsg("已保存");
    } catch {
      setWsMsg("保存失败（远程不可改设置，仅本机可改）");
    }
  }

  async function pick(t: ThemeMode) {
    onTheme(t);
    try {
      await apiSetTheme(t);
      setMsg("已保存");
    } catch {
      setMsg("未持久化（远程不可改设置，仅本次会话生效）");
    }
  }

  async function pickConfirm(v: ConfirmLevel) {
    setConfirmLv(v);
    setCfMsg("");
    try {
      await apiSetConfirmLevel(v);
      setCfMsg("已保存");
    } catch {
      setCfMsg("保存失败（远程不可改设置，仅本机可改）");
    }
  }

  async function saveSys() {
    setSysMsg("");
    try {
      await apiSetSysPrompt(sys);
      setSysMsg("已保存，对所有对话/所有 API 生效");
    } catch {
      setSysMsg("保存失败（远程不可改设置，仅本机可改）");
    }
  }

  return (
    <div className="page">
      <h1>设置</h1>

      <section className="set-block">
        <div className="set-title">主题</div>
        <div className="seg">
          {THEMES.map((t) => (
            <button
              key={t.v}
              className={"seg-btn" + (theme === t.v ? " on" : "")}
              onClick={() => pick(t.v)}
            >
              {t.label}
            </button>
          ))}
        </div>
        {!canSettings && (
          <div className="cfg-note warn">
            远程访问下设置不持久化（防止远程自我解锁），仅本次会话生效。
          </div>
        )}
        <div className="cfg-msg">{msg}</div>
        <div className="muted" style={{ marginTop: 8 }}>
          注：代码高亮配色暂固定深色,亮色主题下代码块仍偏深,后续再适配。
        </div>
      </section>

      <section className="set-block">
        <div className="set-title">高危操作确认</div>
        <div className="seg">
          {CONFIRMS.map((c) => (
            <button
              key={c.v}
              className={"seg-btn" + (confirmLv === c.v ? " on" : "")}
              onClick={() => pickConfirm(c.v)}
            >
              {c.label}
            </button>
          ))}
        </div>
        <div className="muted" style={{ marginTop: 8 }}>
          {CONFIRMS.find((c) => c.v === confirmLv)?.hint}
          。编程 Agent 每次任务开始有 Git 检查点，可一键回滚。
        </div>
        <div className="cfg-msg">{cfMsg}</div>
      </section>

      <section className="set-block">
        <div className="set-title">
          本地 API 聚合代理
          <span className={"dot " + (proxy?.enabled ? "ok" : "err")} />
          {proxy?.enabled ? "在线" : "已关闭"}
        </div>
        <div className="muted">
          把所有配置过 key 的 API 聚合成一个 OpenAI 兼容端点,其他工具
          (Cline / Cursor / NextChat 等) 填这个地址就能用本机所有模型。
        </div>
        {proxy && (
          <>
            <label>
              Base URL(复制给客户端)
              <div className="preset-row">
                <input
                  readOnly
                  value={`http://${proxy.host}:${proxy.port}/v1`}
                />
                <button
                  onClick={() => {
                    navigator.clipboard.writeText(
                      `http://${proxy.host}:${proxy.port}/v1`,
                    );
                    setProxyMsg("base_url 已复制");
                  }}
                >
                  复制
                </button>
              </div>
            </label>
            <label>
              API Key
              <div className="preset-row">
                <input readOnly value={proxy.key} />
                <button
                  onClick={() => {
                    navigator.clipboard.writeText(proxy.key);
                    setProxyMsg("key 已复制");
                  }}
                >
                  复制
                </button>
                <button
                  className="danger"
                  onClick={async () => {
                    if (
                      !confirm(
                        "重置 key 后,所有已在用客户端将失效,需要重新配置。继续?",
                      )
                    )
                      return;
                    try {
                      const r = await regenProxyKey();
                      setProxy({ ...proxy, key: r.key });
                      setProxyMsg("key 已重置");
                    } catch (e) {
                      setProxyMsg((e as Error).message);
                    }
                  }}
                >
                  重置
                </button>
              </div>
            </label>
            <div className="muted">
              当前可用模型 {proxy.models_count} 个;关闭代理则
              <code> /v1/* </code>
              端点返回 503。
            </div>
            <div className="cfg-actions">
              <button
                onClick={async () => {
                  try {
                    const r = await toggleProxy(!proxy.enabled);
                    setProxy({ ...proxy, enabled: r.enabled });
                    setProxyMsg(r.enabled ? "已启用" : "已关闭");
                  } catch (e) {
                    setProxyMsg((e as Error).message);
                  }
                }}
              >
                {proxy.enabled ? "关闭代理" : "启用代理"}
              </button>
              <span className="cfg-msg">{proxyMsg}</span>
            </div>
          </>
        )}
      </section>


      <SearchPanel canSettings={canSettings} />

      <section className="set-block">
        <div className="set-title">会话工作区</div>
        <div className="muted" style={{ marginBottom: 8 }}>
          「当前工作目录」是会话的根目录(工作基地):读/搜全盘自由,
          但根目录之外的写/删/命令永远先弹确认。根目录是 git 仓库时
          自动有检查点/回滚安全网(非 git 也能用,只是没有安全网)。
        </div>
        {/* Git 检测:没装就引导安装 */}
        {gitSt && !gitSt.installed && canSettings && (
          <div className="cfg-note warn" style={{ marginBottom: 8 }}>
            <div>
              ⚠ 未检测到 Git。Agent 需要 Git 做检查点/回滚才能用。
              下面填好下载链接与安装目录,点「下载并静默安装」一次搞定。
            </div>
            <div className="preset-row" style={{ marginTop: 8 }}>
              <input
                value={gitUrl}
                onChange={(e) => setGitUrl(e.target.value)}
                placeholder="Git for Windows 安装包 URL(可填官方/镜像直链)"
              />
            </div>
            <div className="muted" style={{ marginTop: 4 }}>
              默认:GitHub 官方;国内可换清华 TUNA 镜像直链,如
              https://mirrors.tuna.tsinghua.edu.cn/github-release/git-for-windows/git/...exe
            </div>
            <div className="preset-row" style={{ marginTop: 8 }}>
              <input
                value={gitDir}
                onChange={(e) => setGitDir(e.target.value)}
                placeholder="安装目录(留空则用系统默认 C:\Program Files\Git)"
              />
              {hasNativePicker() && (
                <button
                  onClick={async () => {
                    const p = await pickFolder();
                    if (p) setGitDir(p);
                  }}
                >
                  浏览…
                </button>
              )}
              <button
                disabled={installing || !gitUrl.trim()}
                onClick={async () => {
                  if (!confirm("将下载并静默安装 Git。耗时可能几分钟,期间请勿关闭。继续?"))
                    return;
                  setInstalling(true);
                  setGitMsg("下载+安装中,请耐心等待…");
                  try {
                    const r = await installGit(gitUrl.trim(), gitDir.trim());
                    if (r.ok) {
                      setGitMsg(
                        "✓ Git 已装" +
                          (r.path ? ` 于 ${r.path}` : "") +
                          (r.note ? `(${r.note})` : "")
                      );
                      try {
                        setGitSt(await getGitStatus());
                      } catch {
                        /* ignore */
                      }
                    } else {
                      setGitMsg("✖ " + (r.error || "安装失败"));
                    }
                  } catch (e) {
                    setGitMsg("✖ " + (e as Error).message);
                  } finally {
                    setInstalling(false);
                  }
                }}
              >
                {installing ? "安装中…" : "下载并静默安装"}
              </button>
            </div>
            {gitMsg && (
              <div className="cfg-msg" style={{ marginTop: 6 }}>
                {gitMsg}
              </div>
            )}
          </div>
        )}
        {gitSt && gitSt.installed && (
          <div className="muted" style={{ marginBottom: 8 }}>
            Git: ✓ {gitSt.version || gitSt.path}
          </div>
        )}
        <div className="muted">授权根目录白名单：</div>
        <div className="toggles" style={{ margin: "6px 0" }}>
          {wsc?.allowed_roots.length ? (
            wsc.allowed_roots.map((r) => (
              <div key={r} className="preset-row">
                <input value={r} readOnly />
                <button
                  className="danger"
                  disabled={!canSettings}
                  onClick={() =>
                    saveWs({
                      allowed_roots: wsc.allowed_roots.filter(
                        (x) => x !== r
                      ),
                    })
                  }
                >
                  ×
                </button>
              </div>
            ))
          ) : (
            <div className="muted">（空——Agent 现在不能动任何目录）</div>
          )}
        </div>
        {canSettings && (
          <div className="preset-row">
            <input
              value={newRoot}
              onChange={(e) => setNewRoot(e.target.value)}
              placeholder="手动输入路径，或点右侧「浏览…」选文件夹"
            />
            {hasNativePicker() && (
              <button
                onClick={async () => {
                  const p = await pickFolder();
                  if (!p) return;
                  // 选完直接加,不用再点「＋ 加」;同时回填输入框作可见反馈
                  setNewRoot(p);
                  await saveWs({
                    allowed_roots: [...(wsc?.allowed_roots ?? []), p],
                  });
                  setNewRoot("");
                }}
                title="打开原生文件夹选择对话框（仅本机 Electron 可用）"
              >
                浏览…
              </button>
            )}
            <button
              onClick={() => {
                if (!newRoot.trim()) return;
                saveWs({
                  allowed_roots: [
                    ...(wsc?.allowed_roots ?? []),
                    newRoot.trim(),
                  ],
                });
                setNewRoot("");
              }}
            >
              ＋ 加
            </button>
          </div>
        )}
        <label>
          当前工作目录（从白名单选）
          <select
            value={wsc?.cwd ?? ""}
            disabled={!canSettings}
            onChange={(e) => saveWs({ cwd: e.target.value })}
          >
            {(wsc?.allowed_roots ?? []).map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </label>
        <div className="muted">
          git 仓库：
          {wsc?.cwd_is_git ? (
            <span style={{ color: "var(--ok)" }}> 是 ✓</span>
          ) : (
            <>
              <span style={{ color: "var(--warn)" }}> 否</span>
              {canSettings && (
                <button
                  className="mini"
                  onClick={async () => {
                    if (!wsc?.cwd) return;
                    try {
                      await gitInitWorkspace(wsc.cwd);
                      setWsc(await getWorkspace());
                      setWsMsg("已初始化为 git 仓库");
                    } catch (e) {
                      setWsMsg((e as Error).message);
                    }
                  }}
                >
                  初始化为 git 仓库
                </button>
              )}
            </>
          )}
        </div>
        <label>
          测试命令（改动后自动跑，失败可一键回滚；留空则跳过）
          <input
            value={wsc?.test_cmd ?? ""}
            disabled={!canSettings}
            onChange={(e) =>
              setWsc(wsc ? { ...wsc, test_cmd: e.target.value } : wsc)
            }
            onBlur={() => wsc && saveWs({ test_cmd: wsc.test_cmd })}
            placeholder="如 pytest -q / npm test"
          />
        </label>
        <div className="cfg-msg">{wsMsg}</div>
      </section>

      <ComponentsPanel canSettings={canSettings} />

      <section className="set-block">
        <div className="set-title">编程 skills（仅「编程」页生效）</div>
        <div className="muted" style={{ marginBottom: 8 }}>
          来自 mattpocock/skills 的工程实践，含「需求盘问」——开启后编程
          Agent 动手前会先追问澄清不清的需求，避免瞎猜生成错代码。
          只注入到「编程」页，普通对话/文件模式不受影响。
          （克隆/更新 skills 在上方「组件与更新」面板。）
        </div>
        {sk && (
          <div className="toggles">
            <Toggle
              on={sk.enabled}
              label={`启用工程 skills（已克隆 ${sk.count} 个，含 ${
                sk.skills.includes("grill-with-docs")
                  ? "grill-with-docs 盘问"
                  : "工程指南"
              }）`}
              disabled={!canSettings}
              onClick={async () => {
                try {
                  setSk(await setSkills(!sk.enabled));
                } catch {
                  setSkMsg("保存失败（远程不可改）");
                }
              }}
            />
            {!sk.cloned && (
              <div className="cfg-note warn">
                未检测到 skills 仓库，到上方「组件与更新」面板点「克隆」。
              </div>
            )}
            {skMsg && <span className="cfg-msg">{skMsg}</span>}
            <div className="muted" style={{ marginTop: 6 }}>
              已加载：{sk.skills.join("、") || "（无）"}
            </div>
          </div>
        )}
      </section>

      <section className="set-block">
        <div className="set-title">全局系统提示词</div>
        <div className="muted" style={{ marginBottom: 8 }}>
          作为 system 消息自动加到每次对话最前面，对所有对话、所有 API
          生效。留空则不加。
        </div>
        <textarea
          className="sys-area"
          value={sys}
          disabled={!canSettings}
          onChange={(e) => setSys(e.target.value)}
          placeholder="例：你是一个简洁、直接的中文助手……"
        />
        {canSettings && (
          <div className="cfg-actions" style={{ marginTop: 8 }}>
            <button onClick={saveSys}>保存</button>
            <span className="cfg-msg">{sysMsg}</span>
          </div>
        )}
      </section>

      <section className="set-block">
        <div className="chat-head">
          <div className="set-title">日志</div>
          <div style={{ display: "flex", gap: 8 }}>
            <button
              className="cfg-toggle"
              onClick={async () => {
                setLogsMsg("加载中…");
                try {
                  const r = await getLogs(300);
                  setLogs(r);
                  setLogsMsg("");
                } catch (e) {
                  setLogsMsg((e as Error).message);
                }
              }}
            >
              查看(最近 300 行)
            </button>
            <button
              className="cfg-toggle"
              disabled={loadingFull}
              onClick={async () => {
                setLoadingFull(true);
                setLogsMsg("载入全量日志中…");
                try {
                  const r = await getLogs(999999);
                  setFullLogs(r.text || "(空)");
                  setLogsMsg("");
                } catch (e) {
                  setLogsMsg((e as Error).message);
                } finally {
                  setLoadingFull(false);
                }
              }}
            >
              {loadingFull ? "载入中…" : "查看全部(弹窗)"}
            </button>
            {canSettings && logs && (
              <button
                className="cfg-toggle"
                onClick={async () => {
                  if (!confirm("清空全部日志?(滚动文件一并清)")) return;
                  try {
                    setLogs(await clearLogs());
                    setLogsMsg("已清空");
                  } catch (e) {
                    setLogsMsg((e as Error).message);
                  }
                }}
              >
                清空
              </button>
            )}
          </div>
        </div>
        <div className="muted" style={{ marginTop: 6 }}>
          日志文件:5MB × 2 份滚动(总上限 ~10MB,超过最早自动覆盖)。
          {logs &&
            ` 当前 ${Math.round(logs.size / 1024)}KB / 上限 ${Math.round(
              logs.max_total / 1024 / 1024
            )}MB · ${logs.path}`}
        </div>
        {logs && (
          <pre
            style={{
              maxHeight: 360,
              overflow: "auto",
              background: "var(--panel-2)",
              border: "1px solid var(--border-2)",
              borderRadius: 7,
              padding: 10,
              fontSize: 12,
              marginTop: 8,
              whiteSpace: "pre-wrap",
              wordBreak: "break-all",
            }}
          >
            {logs.text || "(空)"}
          </pre>
        )}
        <div className="cfg-msg">{logsMsg}</div>
      </section>

      {fullLogs !== null && (
        <div className="log-modal" onClick={() => setFullLogs(null)}>
          <div
            className="log-modal-body"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="log-modal-head">
              <div className="set-title">全部日志</div>
              <button onClick={() => setFullLogs(null)}>关闭</button>
            </div>
            <pre>{fullLogs}</pre>
          </div>
        </div>
      )}
    </div>
  );
}

function Toggle({
  on,
  label,
  onClick,
  disabled,
}: {
  on: boolean;
  label: string;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      className={"tg" + (on ? " on" : "")}
      onClick={onClick}
      disabled={disabled}
    >
      <span className="tg-knob" />
      {label}
    </button>
  );
}
