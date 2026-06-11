import { useEffect, useState } from "react";
import {
  deleteProvider,
  discoverProviderModels,
  getProviders,
  getUsage,
  resetUsage,
  setActive,
  testProvider,
  upsertProvider,
  getProviderBalance,
  type BalanceResult,
  type ProvidersState,
  type ProviderTest,
  type UsageState,
  type WhoAmI,
} from "../api";

interface PresetRow {
  label: string;
  model: string;
  extra: string; // JSON 文本
  pinned?: boolean;
  description?: string;
}
interface Draft {
  id?: string;
  name: string;
  format: string;
  base_url: string;
  api_key: string;
  api_key_set: boolean;
  capability: string;
  presets: PresetRow[];
}

const FORMATS = ["openai_compat", "anthropic", "gemini", "custom"];

function blankDraft(): Draft {
  return {
    name: "",
    format: "openai_compat",
    base_url: "",
    api_key: "",
    api_key_set: false,
    capability: "",
    presets: [{ label: "默认", model: "", extra: "" }],
  };
}

export default function ApiPage({ who }: { who: WhoAmI | null }) {
  const canManage = who ? who.permissions.api_manage !== false : true;
  const [ps, setPs] = useState<ProvidersState | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [err, setErr] = useState("");
  const [usage, setUsage] = useState<UsageState | null>(null);
  const [testing, setTesting] = useState<Record<string, boolean>>({});
  const [tested, setTested] = useState<Record<string, ProviderTest>>({});
  const [balances, setBalances] = useState<Record<string, BalanceResult>>({});
  const [balLoading, setBalLoading] = useState<Record<string, boolean>>({});

  async function reload() {
    try {
      setPs(await getProviders());
    } catch {
      /* ignore */
    }
    try {
      setUsage(await getUsage());
    } catch {
      /* ignore */
    }
  }
  useEffect(() => {
    reload();
  }, []);

  async function loadBalance(pid: string) {
    setBalLoading((m) => ({ ...m, [pid]: true }));
    try {
      const r = await getProviderBalance(pid);
      setBalances((m) => ({ ...m, [pid]: r }));
    } catch (e) {
      setBalances((m) => ({
        ...m,
        [pid]: {
          ok: false,
          supported: true,
          error: (e as Error).message,
        },
      }));
    } finally {
      setBalLoading((m) => ({ ...m, [pid]: false }));
    }
  }

  // 列表加载后自动拉一次余量(只对已配 key 的 provider,而且不阻塞 UI)
  useEffect(() => {
    if (!ps) return;
    for (const p of ps.providers) {
      if (p.api_key_set && balances[p.id] === undefined) {
        void loadBalance(p.id);
      }
    }
    // 故意不依赖 balances 防止循环;新 provider 触发 ps 更新会重跑
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ps]);

  async function runTest(p: { id: string; presets: { label: string }[] }) {
    const label =
      ps && ps.active.provider_id === p.id
        ? ps.active.preset_label
        : p.presets[0]?.label || "";
    setTesting((m) => ({ ...m, [p.id]: true }));
    setTested((m) => {
      const n = { ...m };
      delete n[p.id];
      return n;
    });
    try {
      const r = await testProvider(p.id, label);
      setTested((m) => ({ ...m, [p.id]: r }));
    } catch (e) {
      setTested((m) => ({
        ...m,
        [p.id]: { ok: false, error: (e as Error).message },
      }));
    } finally {
      setTesting((m) => ({ ...m, [p.id]: false }));
    }
  }

  async function clearUsage() {
    try {
      setUsage(await resetUsage());
    } catch {
      /* ignore */
    }
  }

  function editProvider(id: string) {
    const p = ps?.providers.find((x) => x.id === id);
    if (!p) return;
    setErr("");
    setDraft({
      id: p.id,
      name: p.name,
      format: p.format,
      base_url: p.base_url,
      api_key: "",
      api_key_set: p.api_key_set,
      capability: p.capability || "",
      presets: p.presets.length
        ? p.presets.map((pr) => ({
            label: pr.label,
            model: pr.model,
            extra: Object.keys(pr.extra_body || {}).length
              ? JSON.stringify(pr.extra_body)
              : "",
            pinned: !!pr.pinned,
            description: pr.description || "",
          }))
        : [{ label: "默认", model: "", extra: "" }],
    });
  }

  async function save() {
    if (!draft) return;
    setErr("");
    const presets = [];
    for (const r of draft.presets) {
      if (!r.label.trim() || !r.model.trim()) {
        setErr("每个预设的「标签」和「模型」必填");
        return;
      }
      let extra_body = {};
      if (r.extra.trim()) {
        try {
          extra_body = JSON.parse(r.extra);
        } catch {
          setErr(`预设「${r.label}」的额外参数不是合法 JSON`);
          return;
        }
      }
      presets.push({
        label: r.label.trim(),
        model: r.model.trim(),
        extra_body,
        pinned: !!r.pinned,
        description: (r.description || "").trim(),
      });
    }
    try {
      await upsertProvider({
        id: draft.id,
        name: draft.name.trim() || "未命名",
        format: draft.format,
        base_url: draft.base_url.trim(),
        api_key: draft.api_key.trim() || undefined,
        capability: draft.capability.trim(),
        presets,
      });
      setDraft(null);
      reload();
    } catch (e) {
      setErr((e as Error).message);
    }
  }

  async function del(id: string) {
    await deleteProvider(id);
    reload();
  }

  async function makeActive(pid: string, label: string) {
    await setActive(pid, label);
    reload();
  }

  return (
    <div className="page">
      <div className="chat-head">
        <h1>API 管理</h1>
        <div className="chat-tools">
          {ps && !draft && (
            <button
              className="cfg-toggle"
              onClick={async () => {
                for (const p of ps.providers) {
                  if (!p.api_key_set) continue;
                  await runTest(p);
                }
              }}
              disabled={Object.values(testing).some(Boolean)}
              title="依次测试所有已配 key 的 API"
            >
              批量测试
            </button>
          )}
          {canManage && !draft && (
            <button
              className="cfg-toggle"
              onClick={() => {
                setErr("");
                setDraft(blankDraft());
              }}
            >
              ＋ 新增 API
            </button>
          )}
        </div>
      </div>

      {!canManage && (
        <div className="cfg-note warn">
          远程访问只读：可切换当前 API，不能新增/编辑/删除（密钥仅本机可见可改）。
        </div>
      )}

      {!draft && !ps && (
        <div className="loading-row">
          <span className="spinner" />
          <span>加载 API 列表中…</span>
        </div>
      )}

      {!draft && ps && (
        <div className="prov-list">
          {ps?.providers.length ? (
            ps.providers.map((p) => {
              const isActive = ps.active.provider_id === p.id;
              return (
                <div
                  key={p.id}
                  className={"prov-card" + (isActive ? " active" : "")}
                >
                  <div className="prov-main">
                    <div className="prov-name">
                      {p.name}
                      {isActive && <span className="badge">当前</span>}
                      {!p.api_key_set && (
                        <span className="badge warn">无 key</span>
                      )}
                    </div>
                    <div className="muted">
                      {p.format} · {p.base_url}
                    </div>
                    {p.capability && (
                      <div className="muted">擅长：{p.capability}</div>
                    )}
                    <ProviderPresetChips
                      provider={p}
                      isActive={isActive}
                      activeLabel={ps.active.preset_label}
                      onPick={(lbl) => makeActive(p.id, lbl)}
                    />
                    <div className="prov-test">
                      <button
                        disabled={!!testing[p.id]}
                        onClick={() => runTest(p)}
                        title="用最小请求实测该 API 能否连通（默认参数）"
                      >
                        {testing[p.id] ? "测试中…" : "测试连通"}
                      </button>
                      {tested[p.id] &&
                        (tested[p.id].ok ? (
                          <span className="badge ok">
                            通 {tested[p.id].ms}ms · {tested[p.id].model}
                          </span>
                        ) : (
                          <span
                            className="badge warn"
                            title={tested[p.id].error}
                          >
                            失败：{tested[p.id].error}
                          </span>
                        ))}
                    </div>
                    {p.api_key_set && (
                      <BalanceBar
                        bal={balances[p.id]}
                        loading={!!balLoading[p.id]}
                        onRefresh={() => loadBalance(p.id)}
                      />
                    )}
                  </div>
                  {canManage && (
                    <div className="prov-actions">
                      <button onClick={() => editProvider(p.id)}>编辑</button>
                      <button className="danger" onClick={() => del(p.id)}>
                        删除
                      </button>
                    </div>
                  )}
                </div>
              );
            })
          ) : (
            <div className="empty">还没有 API，点右上「＋ 新增 API」。</div>
          )}
        </div>
      )}

      {draft && (
        <div className="cfg-box">
          <label>
            名称
            <input
              value={draft.name}
              onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              placeholder="DeepSeek"
            />
          </label>
          <label>
            格式
            <select
              value={draft.format}
              onChange={(e) => setDraft({ ...draft, format: e.target.value })}
            >
              {FORMATS.map((f) => (
                <option key={f} value={f}>
                  {f}
                  {f === "openai_compat" ? "（当前已支持）" : "（后续阶段）"}
                </option>
              ))}
            </select>
          </label>
          <label>
            base_url
            <input
              value={draft.base_url}
              onChange={(e) =>
                setDraft({ ...draft, base_url: e.target.value })
              }
              placeholder="https://api.deepseek.com"
            />
          </label>
          <label>
            api_key
            <input
              type="password"
              value={draft.api_key}
              onChange={(e) =>
                setDraft({ ...draft, api_key: e.target.value })
              }
              placeholder={
                draft.api_key_set ? "已配置（留空=不改）" : "未配置，请填入"
              }
            />
          </label>

          <label>
            擅长描述（给本地模型自动路由参考，如「推理/代码强，中文写作一般」）
            <input
              value={draft.capability}
              onChange={(e) =>
                setDraft({ ...draft, capability: e.target.value })
              }
              placeholder="推理、代码、数学较强，长文本与中文不错"
            />
          </label>

          <div className="set-title">预设（模型 + 模式）</div>
          <div className="cfg-actions" style={{ marginBottom: 6 }}>
            <button
              type="button"
              onClick={async () => {
                const base = draft.base_url.trim();
                if (!base) {
                  setErr("先填 base_url 再发现模型");
                  return;
                }
                setErr("发现中…");
                try {
                  const r = await discoverProviderModels(base, {
                    api_key: draft.api_key.trim() || undefined,
                  });
                  if (!r.models.length) {
                    const errs = (r.errors || []).join(" | ");
                    let hint: string;
                    if (errs.toLowerCase().includes("user location")) {
                      hint =
                        "上游拒绝当前出口地区(User location is not supported)—— 不是 key 问题,常见于 Gemini 等对地区敏感的服务。详情:" +
                        errs.slice(0, 400);
                    } else if (errs.includes("401") || errs.includes("403")) {
                      hint =
                        "API key 鉴权失败(401/403),检查 key 是否正确、是否已启用。详情:" +
                        errs.slice(0, 400);
                    } else if (errs) {
                      hint = "未发现模型 — 详情:" + errs.slice(0, 400);
                    } else {
                      hint = "未发现模型(检查 base_url 是否正确)";
                    }
                    setErr(hint);
                    return;
                  }
                  // 合并到预设(去重),label = model = 发现到的名字
                  const exist = new Set(draft.presets.map((p) => p.model));
                  const add = r.models
                    .filter((m) => !exist.has(m))
                    .map((m) => ({ label: m, model: m, extra: "" }));
                  setDraft({
                    ...draft,
                    presets: [
                      ...draft.presets.filter((p) => p.label || p.model),
                      ...add,
                    ],
                  });
                  setErr(`已新增 ${add.length} 个预设(去重)`);
                } catch (e) {
                  setErr("发现失败：" + (e as Error).message);
                }
              }}
              title="从当前 base_url 自动发现模型列表(Ollama /api/tags 或 OpenAI /v1/models)"
            >
              自动发现模型
            </button>
          </div>
          {draft.presets.map((r, i) => (
            <div
              key={i}
              className="preset-row"
              style={{
                flexDirection: "column",
                alignItems: "stretch",
                gap: 4,
              }}
            >
              <div
                style={{ display: "flex", gap: 6, alignItems: "center" }}
              >
                <button
                  type="button"
                  title={r.pinned ? "已置顶 — 点击取消" : "置顶此预设(聚合器仅置顶可被自动路由)"}
                  onClick={() => {
                    const ps2 = [...draft.presets];
                    ps2[i] = { ...r, pinned: !r.pinned };
                    setDraft({ ...draft, presets: ps2 });
                  }}
                  style={{
                    padding: "4px 8px",
                    color: r.pinned ? "#f59f00" : "var(--muted)",
                    fontSize: 16,
                  }}
                >
                  {r.pinned ? "★" : "☆"}
                </button>
                <input
                  placeholder="标签 如 V4 Pro·思考"
                  value={r.label}
                  style={{ flex: "1 1 140px" }}
                  onChange={(e) => {
                    const ps2 = [...draft.presets];
                    ps2[i] = { ...r, label: e.target.value };
                    setDraft({ ...draft, presets: ps2 });
                  }}
                />
                <input
                  placeholder="模型 id 如 deepseek-v4-pro"
                  value={r.model}
                  style={{ flex: "2 1 220px" }}
                  onChange={(e) => {
                    const ps2 = [...draft.presets];
                    ps2[i] = { ...r, model: e.target.value };
                    setDraft({ ...draft, presets: ps2 });
                  }}
                />
                <input
                  placeholder='额外参数JSON 如 {"thinking":{"type":"enabled"}}'
                  value={r.extra}
                  style={{ flex: "1 1 200px" }}
                  onChange={(e) => {
                    const ps2 = [...draft.presets];
                    ps2[i] = { ...r, extra: e.target.value };
                    setDraft({ ...draft, presets: ps2 });
                  }}
                />
                <button
                  className="danger"
                  onClick={() =>
                    setDraft({
                      ...draft,
                      presets: draft.presets.filter((_, j) => j !== i),
                    })
                  }
                >
                  ×
                </button>
              </div>
              <input
                placeholder="描述(可选) — 例如「便宜快;闲聊用」「推理强;复杂任务用」(自动路由会读这条)"
                value={r.description || ""}
                style={{ marginLeft: 32, fontSize: 12 }}
                onChange={(e) => {
                  const ps2 = [...draft.presets];
                  ps2[i] = { ...r, description: e.target.value };
                  setDraft({ ...draft, presets: ps2 });
                }}
              />
            </div>
          ))}
          <button
            onClick={() =>
              setDraft({
                ...draft,
                presets: [
                  ...draft.presets,
                  { label: "", model: "", extra: "" },
                ],
              })
            }
          >
            ＋ 加预设
          </button>

          {err && <div className="cfg-note warn">{err}</div>}
          <div className="cfg-actions">
            <button onClick={save}>保存</button>
            <button onClick={() => setDraft(null)}>取消</button>
          </div>
        </div>
      )}

      {!draft && (
        <div className="set-block" style={{ marginTop: 18 }}>
          <div className="chat-head">
            <div className="set-title">Token 用量统计</div>
            {usage && usage.totals.calls > 0 && (
              <button className="cfg-toggle" onClick={clearUsage}>
                清零
              </button>
            )}
          </div>
          {usage && usage.rows.length ? (
            <>
              <table className="usage-tbl">
                <thead>
                  <tr>
                    <th>API</th>
                    <th>调用次数</th>
                    <th>输入 tokens</th>
                    <th>输出 tokens</th>
                    <th>合计 tokens</th>
                  </tr>
                </thead>
                <tbody>
                  {usage.rows.map((r) => (
                    <tr key={r.name}>
                      <td>{r.name}</td>
                      <td>{r.calls}</td>
                      <td>{r.prompt_tokens.toLocaleString()}</td>
                      <td>{r.completion_tokens.toLocaleString()}</td>
                      <td>{r.total_tokens.toLocaleString()}</td>
                    </tr>
                  ))}
                  <tr className="usage-total">
                    <td>合计</td>
                    <td>{usage.totals.calls}</td>
                    <td>{usage.totals.prompt_tokens.toLocaleString()}</td>
                    <td>
                      {usage.totals.completion_tokens.toLocaleString()}
                    </td>
                    <td>{usage.totals.total_tokens.toLocaleString()}</td>
                  </tr>
                </tbody>
              </table>
              <div className="muted" style={{ marginTop: 8 }}>
                按各 API 返回的 usage 累计；本地 Ollama 多数不返 usage 故不计。
                {usage.updated && `（更新于 ${usage.updated}）`}
              </div>
            </>
          ) : (
            <div className="muted">
              暂无用量记录。发起对话后，支持 usage 的云端 API 会自动累计。
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// 与后端 is_aggregator 同步
const AGG_HOSTS = [
  "openrouter.ai", "together.xyz", "siliconflow",
  "deepinfra.com", "huggingface.co", "portkey.ai", "anyscale.com",
];
function isAggregator(base_url: string): boolean {
  try {
    const h = new URL(base_url).hostname.toLowerCase();
    return AGG_HOSTS.some((k) => h.includes(k));
  } catch {
    return false;
  }
}

function ProviderPresetChips({
  provider,
  isActive,
  activeLabel,
  onPick,
}: {
  provider: import("../api").ProviderInfo;
  isActive: boolean;
  activeLabel: string;
  onPick: (label: string) => void;
}) {
  const agg = isAggregator(provider.base_url);
  const [showAll, setShowAll] = useState(false);
  const pinned = provider.presets.filter((p) => p.pinned);
  // 聚合器:默认只显示 pinned;非聚合器或没置顶时显示全部(但有上限)
  let visible = provider.presets;
  if (agg && !showAll) visible = pinned;
  const cap = agg ? 12 : 30;
  const truncated = visible.length > cap;
  if (truncated) visible = visible.slice(0, cap);
  return (
    <div>
      <div className="prov-presets">
        {visible.map((pr) => (
          <button
            key={pr.label}
            className={
              "chip" +
              (isActive && activeLabel === pr.label ? " on" : "")
            }
            onClick={() => onPick(pr.label)}
            title={
              pr.description
                ? `${pr.model} — ${pr.description}`
                : pr.model
            }
          >
            {pr.pinned && <span style={{ color: "#f59f00" }}>★</span>}
            {pr.label}
          </button>
        ))}
        {agg && pinned.length === 0 && !showAll && (
          <span className="muted" style={{ fontSize: 12 }}>
            (聚合器 — 请到编辑页 ★ 置顶常用的几个,自动路由也仅使用置顶)
          </span>
        )}
      </div>
      {agg && provider.presets.length > pinned.length && (
        <button
          type="button"
          onClick={() => setShowAll((v) => !v)}
          className="cfg-toggle"
          style={{ fontSize: 11, padding: "2px 8px", marginTop: 4 }}
        >
          {showAll
            ? `↑ 折叠(只显示 ${pinned.length} 个置顶)`
            : `↓ 显示全部 ${provider.presets.length} 个模型`}
        </button>
      )}
      {truncated && !agg && (
        <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>
          已显示前 {cap} 个,完整请在编辑页查看
        </div>
      )}
    </div>
  );
}

function fmtMoney(n: number, currency = "USD"): string {
  const sym = currency === "CNY" ? "¥" : currency === "USD" ? "$" : "";
  if (n >= 100) return `${sym}${n.toFixed(0)} ${sym ? "" : currency}`.trim();
  if (n >= 1) return `${sym}${n.toFixed(2)} ${sym ? "" : currency}`.trim();
  return `${sym}${n.toFixed(4)} ${sym ? "" : currency}`.trim();
}

function BalanceBar({
  bal,
  loading,
  onRefresh,
}: {
  bal: BalanceResult | undefined;
  loading: boolean;
  onRefresh: () => void;
}) {
  if (loading && !bal) {
    return (
      <div
        className="muted"
        style={{ fontSize: 12, marginTop: 6, display: "flex", gap: 6 }}
      >
        <span
          className="spinner"
          style={{ width: 11, height: 11, borderWidth: 2 }}
        />
        查询余量中…
      </div>
    );
  }
  if (!bal) return null;
  if (!bal.supported) {
    return (
      <div
        className="muted"
        style={{ fontSize: 12, marginTop: 6 }}
        title={bal.error || ""}
      >
        余量：该 host 未提供查询端点
      </div>
    );
  }
  if (!bal.ok) {
    return (
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          marginTop: 6,
          fontSize: 12,
        }}
      >
        <span className="badge warn" title={bal.error || ""}>
          余量查询失败
        </span>
        <button
          onClick={onRefresh}
          disabled={loading}
          style={{ padding: "2px 8px", fontSize: 11 }}
        >
          {loading ? "刷新中" : "重试"}
        </button>
        {bal.error && (
          <span
            className="muted"
            style={{
              fontSize: 11,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              maxWidth: 320,
            }}
          >
            {bal.error}
          </span>
        )}
      </div>
    );
  }
  const cur = bal.currency || "USD";
  const total = bal.total;
  const used = bal.used;
  const remain = bal.remaining;
  const pct =
    total && total > 0 && remain != null
      ? Math.max(0, Math.min(100, (remain / total) * 100))
      : null;
  const lowOnFumes = pct != null && pct < 15;
  return (
    <div style={{ marginTop: 8, fontSize: 12 }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          marginBottom: 3,
        }}
      >
        <span className="muted">余量</span>
        {remain != null && (
          <span
            style={{
              fontWeight: 600,
              color: lowOnFumes ? "#c92a2a" : "var(--text)",
            }}
          >
            {fmtMoney(remain, cur)}
          </span>
        )}
        {total != null && (
          <span className="muted">/ {fmtMoney(total, cur)}</span>
        )}
        {used != null && total == null && (
          <span className="muted">已用 {fmtMoney(used, cur)}</span>
        )}
        <button
          onClick={onRefresh}
          disabled={loading}
          title="刷新余量"
          style={{
            marginLeft: "auto",
            padding: "1px 8px",
            fontSize: 11,
            opacity: loading ? 0.6 : 1,
          }}
        >
          {loading ? "…" : "↻"}
        </button>
      </div>
      {pct != null && (
        <div
          style={{
            height: 6,
            background: "var(--panel-2)",
            border: "1px solid var(--border-2)",
            borderRadius: 4,
            overflow: "hidden",
          }}
        >
          <div
            style={{
              width: `${pct}%`,
              height: "100%",
              background: lowOnFumes
                ? "#c92a2a"
                : pct < 40
                ? "#f59f00"
                : "#2f9e44",
              transition: "width .3s",
            }}
          />
        </div>
      )}
    </div>
  );
}
