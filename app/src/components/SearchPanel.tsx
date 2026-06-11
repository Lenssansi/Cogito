// 联网搜索独立配置 —— 不与 API 管理共享 key,自成一套。
// 仅本机可编辑(key 是凭证)。带测试按钮 + 错误诊断。
import { useEffect, useState } from "react";
import {
  type SearchCfg,
  type SearchTestResult,
  getSearchCfg,
  saveSearchCfg,
  testSearch,
} from "../api";

export default function SearchPanel({
  canSettings,
}: {
  canSettings: boolean;
}) {
  const [cfg, setCfg] = useState<SearchCfg | null>(null);
  const [draftKey, setDraftKey] = useState("");
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<SearchTestResult | null>(null);
  const [msg, setMsg] = useState("");

  async function reload() {
    try {
      setCfg(await getSearchCfg());
    } catch (e) {
      setMsg("加载失败:" + (e as Error).message);
    }
  }
  useEffect(() => {
    reload();
  }, []);

  async function save(patch: Parameters<typeof saveSearchCfg>[0]) {
    try {
      setCfg(await saveSearchCfg(patch));
      setMsg("已保存");
      setDraftKey("");
    } catch (e) {
      setMsg("失败:" + (e as Error).message);
    }
  }

  async function runTest() {
    setTesting(true);
    setResult(null);
    setMsg("");
    try {
      const r = await testSearch("Tavily 是什么");
      setResult(r);
    } catch (e) {
      setMsg("测试失败:" + (e as Error).message);
    } finally {
      setTesting(false);
    }
  }

  if (!cfg) {
    return (
      <section className="set-block">
        <div className="set-title">联网搜索</div>
        <div className="muted">加载中…</div>
      </section>
    );
  }

  return (
    <section className="set-block">
      <div className="set-title">联网搜索</div>
      <div className="muted" style={{ marginBottom: 8 }}>
        独立配置,**与 API 管理完全独立**(两边互不可见、可分别存同一 key)。
        推荐 <strong>Tavily</strong>:专为 LLM 设计,免费 1000 次/月,无需信用卡。
        (国内访问 Tavily 可能需自备代理)。注册 <a
          href="https://tavily.com"
          target="_blank"
          rel="noreferrer"
        >tavily.com</a> → API Keys → 复制 <code>tvly-...</code> 粘到下方。
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <label>
          <div className="muted" style={{ fontSize: 11, marginBottom: 2 }}>
            服务商
          </div>
          <select
            value={cfg.provider}
            disabled={!canSettings}
            onChange={(e) => save({ provider: e.target.value })}
            style={{ width: 200 }}
          >
            <option value="tavily">Tavily(推荐)</option>
            <option value="off">关闭联网搜索</option>
          </select>
        </label>
        {cfg.provider !== "off" && (
          <label>
            <div className="muted" style={{ fontSize: 11, marginBottom: 2 }}>
              API Key {cfg.api_key_set && <span style={{ color: "#2f9e44" }}>(已设)</span>}
            </div>
            <div style={{ display: "flex", gap: 6 }}>
              <input
                type="password"
                value={draftKey}
                disabled={!canSettings}
                placeholder={cfg.api_key_set ? "已存,留空=不改" : "tvly-..."}
                onChange={(e) => setDraftKey(e.target.value)}
                style={{ flex: 1, fontFamily: "monospace" }}
              />
              <button
                disabled={!canSettings || !draftKey.trim()}
                onClick={() => save({ api_key: draftKey })}
              >
                保存 key
              </button>
              {cfg.api_key_set && (
                <button
                  className="danger"
                  disabled={!canSettings}
                  onClick={() => save({ api_key: "__clear__" })}
                >
                  清除
                </button>
              )}
            </div>
          </label>
        )}
        <label>
          <div className="muted" style={{ fontSize: 11, marginBottom: 2 }}>
            每次结果数(1-10)
          </div>
          <input
            type="number"
            min={1}
            max={10}
            value={cfg.max_results}
            disabled={!canSettings}
            onChange={(e) => save({ max_results: parseInt(e.target.value) })}
            style={{ width: 80 }}
          />
        </label>

      </div>

      <div className="cfg-actions" style={{ marginTop: 10 }}>
        <button
          onClick={runTest}
          disabled={testing || cfg.provider === "off" || !cfg.api_key_set}
        >
          {testing ? "测试中…" : "测试搜索"}
        </button>
        {msg && <span className="cfg-msg">{msg}</span>}
      </div>

      {result && (
        <div
          style={{
            marginTop: 10,
            padding: 8,
            border: "1px solid var(--border-2)",
            borderRadius: 6,
            background: "var(--bg)",
            fontSize: 12,
          }}
        >
          <div style={{ fontWeight: 600, marginBottom: 4 }}>
            {result.ok ? (
              <span style={{ color: "#2f9e44" }}>
                ✓ 通过 · {result.provider} · 返回 {result.count} 条
              </span>
            ) : (
              <span style={{ color: "#c92a2a" }}>
                ✖ 失败 · {result.error}
              </span>
            )}
          </div>
          {result.results?.slice(0, 3).map((r, i) => (
            <div key={i} style={{ marginBottom: 6 }}>
              <div style={{ fontWeight: 500 }}>{r.title}</div>
              <div className="muted" style={{ fontSize: 11 }}>
                {r.snippet.slice(0, 200)}
              </div>
              <div style={{ fontSize: 11, color: "#1971c2" }}>{r.url}</div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
