// 大脑后端切换 —— 本地 Ollama 还是借用某个 provider 的 preset(便宜 Flash
// 类模型当大脑,效果通常优于本地 3B,代价仅 ¥1-2/月)。带综合测试按钮。
import { useEffect, useMemo, useState } from "react";
import {
  type BrainCfg,
  type BrainTestResult,
  type ProvidersState,
  getProviders,
  saveBrain,
  testBrain,
} from "../api";

export default function BrainBackendPanel({
  brain,
  onBrainChange,
  canSettings,
}: {
  brain: BrainCfg | null;
  onBrainChange: (b: BrainCfg) => void;
  canSettings: boolean;
}) {
  const [ps, setPs] = useState<ProvidersState | null>(null);
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<BrainTestResult | null>(null);
  const [msg, setMsg] = useState("");

  useEffect(() => {
    getProviders().then(setPs).catch(() => setPs(null));
  }, []);

  const backend: "local" | "cloud" = brain?.backend === "cloud"
    ? "cloud"
    : "local";
  const selectedProvider = ps?.providers.find(
    (p) => p.id === brain?.cloud_provider_id,
  );
  const presets = selectedProvider?.presets || [];

  const tokenHint = useMemo(() => {
    if (backend !== "cloud" || !selectedProvider) return "";
    const host = selectedProvider.base_url.toLowerCase();
    if (host.includes("deepseek")) return "≈ ¥1-2/月(便宜)";
    if (host.includes("moonshot")) return "≈ ¥3-5/月";
    if (host.includes("openrouter")) return "看具体模型,选 Flash 类 ≈ ¥2-5/月";
    if (host.includes("anthropic")) return "Haiku ≈ ¥10-15/月;Sonnet ¥30+/月";
    if (host.includes("openai")) return "GPT 类约 ¥50-150/月,贵";
    if (host.includes("googleapis")) return "Gemini Flash 类 ≈ ¥1-3/月";
    return "按你选的模型而定 —— Flash/Lite 类便宜,Pro/Sonnet 类贵";
  }, [backend, selectedProvider]);

  async function patchBrain(p: Partial<BrainCfg>) {
    try {
      const r = await saveBrain({ brain: p });
      onBrainChange(r.brain);
    } catch (e) {
      setMsg("保存失败:" + (e as Error).message);
    }
  }

  async function runTest() {
    setTesting(true);
    setResult(null);
    setMsg("");
    try {
      const r = await testBrain();
      setResult(r);
    } catch (e) {
      setMsg("测试调用失败:" + (e as Error).message);
    } finally {
      setTesting(false);
    }
  }

  if (!brain) return null;

  return (
    <div
      style={{
        marginTop: 12,
        padding: 12,
        border: "1px solid var(--border-2)",
        borderRadius: 8,
        background: "var(--panel-2)",
      }}
    >
      <div className="set-title" style={{ fontSize: 14, marginBottom: 6 }}>
        大脑后端
      </div>
      <div className="muted" style={{ fontSize: 12, marginBottom: 8 }}>
        谁来做「路由 / 简单话直答 / 长对话摘要」。默认本地 Ollama 免费;
        换便宜云端 Flash 类模型,路由更准、JSON 输出更稳,代价仅 ¥1-2/月。
      </div>
      <div style={{ display: "flex", gap: 16, alignItems: "center" }}>
        <label
          style={{
            display: "flex",
            gap: 6,
            alignItems: "center",
            cursor: "pointer",
          }}
        >
          <input
            type="radio"
            checked={backend === "local"}
            disabled={!canSettings}
            onChange={() => patchBrain({ backend: "local" })}
          />
          本地 Ollama
        </label>
        <label
          style={{
            display: "flex",
            gap: 6,
            alignItems: "center",
            cursor: "pointer",
          }}
        >
          <input
            type="radio"
            checked={backend === "cloud"}
            disabled={!canSettings}
            onChange={() => patchBrain({ backend: "cloud" })}
          />
          云端 provider(借用一个 preset)
        </label>
      </div>

      {backend === "cloud" && (
        <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 6 }}>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <label style={{ display: "flex", flexDirection: "column", gap: 2, flex: "1 1 200px" }}>
              <span className="muted" style={{ fontSize: 11 }}>Provider</span>
              <select
                value={brain.cloud_provider_id || ""}
                disabled={!canSettings}
                onChange={(e) => patchBrain({
                  cloud_provider_id: e.target.value,
                  cloud_preset_label: "",
                })}
              >
                <option value="">— 选 —</option>
                {ps?.providers.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}{p.api_key_set ? "" : "(无 key)"}
                  </option>
                ))}
              </select>
            </label>
            <label style={{ display: "flex", flexDirection: "column", gap: 2, flex: "1 1 200px" }}>
              <span className="muted" style={{ fontSize: 11 }}>预设(模型)</span>
              <select
                value={brain.cloud_preset_label || ""}
                disabled={!canSettings || !selectedProvider}
                onChange={(e) => patchBrain({
                  cloud_preset_label: e.target.value,
                })}
              >
                <option value="">— 选 —</option>
                {presets.map((pr) => (
                  <option key={pr.label} value={pr.label}>
                    {pr.label}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {tokenHint && (
            <div
              className="muted"
              style={{
                fontSize: 12,
                color: tokenHint.includes("贵")
                  ? "#e8590c"
                  : "var(--muted)",
              }}
            >
              💰 预估开销:{tokenHint}
            </div>
          )}
        </div>
      )}

      <div className="cfg-actions" style={{ marginTop: 10 }}>
        <button
          onClick={runTest}
          disabled={testing || (backend === "cloud" && !brain.cloud_preset_label)}
        >
          {testing ? "测试中…" : "测试当前大脑"}
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
            总评:
            <span style={{
              color: result.overall === "pass" ? "#2f9e44" : "#c92a2a",
              marginLeft: 6,
            }}>
              {result.overall === "pass" ? "✓ 通过" : "✖ 失败"}
            </span>
            <span className="muted" style={{ marginLeft: 8, fontWeight: 400 }}>
              {result.backend}
            </span>
          </div>
          {result.checks.map((c, i) => (
            <div key={i} style={{ marginBottom: 3 }}>
              {c.ok ? "✓" : "✖"} <strong>{c.name}</strong>{" "}
              <span className="muted">{c.detail}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
