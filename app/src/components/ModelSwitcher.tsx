// 统一模型切换器 —— 对话/编程/API 管理三处都用这个
// 形态:按钮触发的下拉,弹层里 provider 折叠分组 + sticky 标题 + 搜索 + 聚合器二级分组
import { useEffect, useMemo, useRef, useState } from "react";
import { type ProvidersState, setActive, togglePresetPin } from "../api";

interface Props {
  ps: ProvidersState | null;
  /** 切换后重渲;父组件传 reload 即可 */
  onChange: () => void | Promise<void>;
  /** 让 button 占满还是按内容 */
  compact?: boolean;
}

// 与后端 is_aggregator 保持一致
const AGGREGATOR_HOSTS = [
  "openrouter.ai", "together.xyz", "siliconflow",
  "deepinfra.com", "huggingface.co", "portkey.ai", "anyscale.com",
];
function isAggregator(base_url: string): boolean {
  try {
    const host = new URL(base_url).hostname.toLowerCase();
    return AGGREGATOR_HOSTS.some((k) => host.includes(k));
  } catch {
    return false;
  }
}

// 聚合器内部 model id 形如 "vendor/model-name",抽 "vendor/" 作为二级组
function vendorGroup(modelId: string): string {
  const i = modelId.indexOf("/");
  return i > 0 ? modelId.slice(0, i) : "(其他)";
}

export default function ModelSwitcher({ ps, onChange, compact }: Props) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  // provider id → expanded
  const [openProv, setOpenProv] = useState<Record<string, boolean>>({});
  // `${pid}::${vendor}` → expanded(聚合器二级)
  const [openVendor, setOpenVendor] = useState<Record<string, boolean>>({});
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const searchRef = useRef<HTMLInputElement | null>(null);

  // 当前选择
  const active = ps?.active;
  const activeProv = ps?.providers.find((p) => p.id === active?.provider_id);
  const activePreset = activeProv?.presets.find(
    (pr) => pr.label === active?.preset_label,
  );
  const activeLabel = activeProv
    ? `${activeProv.name} · ${activePreset?.label || "?"}`
    : "选模型";

  // 默认:展开当前 active provider,聚合器默认展开"全部"(也方便看)
  useEffect(() => {
    if (!ps || !open) return;
    if (active?.provider_id && openProv[active.provider_id] === undefined) {
      setOpenProv((m) => ({ ...m, [active.provider_id]: true }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, ps]);

  // 点击外部关闭
  useEffect(() => {
    if (!open) return;
    const h = (e: MouseEvent) => {
      if (!wrapRef.current) return;
      if (!wrapRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [open]);

  // Esc 关闭 + 打开时聚焦搜索
  useEffect(() => {
    if (!open) return;
    const h = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", h);
    const t = setTimeout(() => searchRef.current?.focus(), 50);
    return () => {
      document.removeEventListener("keydown", h);
      clearTimeout(t);
    };
  }, [open]);

  const groups = useMemo(() => {
    if (!ps) return [];
    const qq = q.trim().toLowerCase();
    const matched = (text: string) =>
      !qq || text.toLowerCase().includes(qq);

    return ps.providers.map((p) => {
      const agg = isAggregator(p.base_url);
      // 一组 preset:按 [pinned 排前] + label/model/description 匹配
      const visible = p.presets.filter(
        (pr) =>
          matched(pr.label) ||
          matched(pr.model) ||
          matched(pr.description || ""),
      );
      // 置顶在前
      const sorted = [...visible].sort((a, b) => {
        if (a.pinned !== b.pinned) return a.pinned ? -1 : 1;
        return a.label.localeCompare(b.label);
      });
      // 聚合器:按 vendor/ 做二级分组(除了置顶项,置顶单独一组顶端)
      let pinned: typeof sorted = [];
      let byVendor: Record<string, typeof sorted> = {};
      if (agg) {
        pinned = sorted.filter((x) => x.pinned);
        const rest = sorted.filter((x) => !x.pinned);
        for (const pr of rest) {
          const v = vendorGroup(pr.model);
          (byVendor[v] = byVendor[v] || []).push(pr);
        }
      }
      return {
        provider: p,
        aggregator: agg,
        visible,
        sorted,
        pinned,
        byVendor,
        // provider 自身是否被 query 隐藏:有任意 preset 匹配 / provider 名字匹配
        matched: visible.length > 0 || matched(p.name),
      };
    });
  }, [ps, q]);

  if (!ps || ps.providers.length === 0) {
    return (
      <span className="cfg-msg">去「API 管理」加一个 API →</span>
    );
  }

  async function choose(pid: string, label: string) {
    await setActive(pid, label);
    setOpen(false);
    setQ("");
    await onChange();
  }

  async function togglePin(pid: string, label: string, pinned: boolean) {
    try {
      await togglePresetPin(pid, label, pinned);
      // 重拉 providers 列表,UI 立刻反映 pinned 变化
      await onChange();
    } catch (e) {
      console.error("toggle pin failed:", e);
    }
  }

  return (
    <div
      ref={wrapRef}
      style={{
        position: "relative",
        display: "inline-block",
        width: compact ? undefined : "100%",
      }}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        title={activeLabel}
        style={{
          width: compact ? undefined : "100%",
          minWidth: 180,
          maxWidth: 360,
          display: "flex",
          alignItems: "center",
          gap: 6,
          padding: "6px 10px",
          overflow: "hidden",
        }}
      >
        <span
          style={{
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            flex: 1,
            textAlign: "left",
            fontSize: 13,
          }}
        >
          {activeLabel}
        </span>
        <span style={{ opacity: 0.6, fontSize: 10 }}>▾</span>
      </button>
      {open && (
        <div
          style={{
            position: "absolute",
            top: "calc(100% + 4px)",
            right: 0,
            width: 460,
            maxHeight: 560,
            background: "var(--bg)",
            border: "1px solid var(--border-2)",
            borderRadius: 8,
            boxShadow: "0 10px 30px rgba(0,0,0,.25)",
            zIndex: 999,
            display: "flex",
            flexDirection: "column",
            overflow: "hidden",
          }}
        >
          <div
            style={{
              padding: 8,
              borderBottom: "1px solid var(--border-2)",
              background: "var(--panel)",
            }}
          >
            <input
              ref={searchRef}
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="搜索模型 / 标签 / 描述 / API 名…"
              style={{ width: "100%", fontSize: 13 }}
            />
            <div
              className="muted"
              style={{ fontSize: 11, marginTop: 4 }}
            >
              ★ = 置顶 · 聚合器仅置顶模型参与自动路由
            </div>
          </div>
          <div style={{ overflow: "auto", flex: 1 }}>
            {groups.filter((g) => g.matched).map((g) => {
              const expanded =
                openProv[g.provider.id] ?? false;
              return (
                <div key={g.provider.id}>
                  <div
                    onClick={() =>
                      setOpenProv((m) => ({
                        ...m,
                        [g.provider.id]: !expanded,
                      }))
                    }
                    style={{
                      position: "sticky",
                      top: 0,
                      zIndex: 2,
                      background: "var(--panel-2)",
                      borderBottom: "1px solid var(--border-2)",
                      padding: "6px 10px",
                      cursor: "pointer",
                      display: "flex",
                      alignItems: "center",
                      gap: 6,
                      fontSize: 13,
                      fontWeight: 600,
                    }}
                  >
                    <span style={{ opacity: 0.6, fontSize: 10 }}>
                      {expanded ? "▾" : "▸"}
                    </span>
                    <span>{g.provider.name}</span>
                    {g.aggregator && (
                      <span
                        className="badge"
                        style={{ fontSize: 10 }}
                      >
                        聚合器
                      </span>
                    )}
                    {!g.provider.api_key_set && (
                      <span
                        className="badge warn"
                        style={{ fontSize: 10 }}
                      >
                        无 key
                      </span>
                    )}
                    <span
                      className="muted"
                      style={{
                        marginLeft: "auto",
                        fontSize: 11,
                        fontWeight: 400,
                      }}
                    >
                      {g.visible.length}/{g.provider.presets.length}
                    </span>
                  </div>
                  {expanded && (
                    <div>
                      {/* 聚合器:置顶单独一组,其余按 vendor 二级 */}
                      {g.aggregator ? (
                        <>
                          {g.pinned.length > 0 && (
                            <VendorBlock
                              title="★ 置顶"
                              sticky
                              items={g.pinned}
                              activeId={active?.preset_label || ""}
                              isActiveProvider={
                                active?.provider_id === g.provider.id
                              }
                              onPick={(lbl) => choose(g.provider.id, lbl)}
                              onPin={(lbl, pin) =>
                                togglePin(g.provider.id, lbl, pin)
                              }
                            />
                          )}
                          {Object.entries(g.byVendor).map(([v, items]) => {
                            const key = `${g.provider.id}::${v}`;
                            const open2 = openVendor[key] ?? false;
                            return (
                              <VendorBlock
                                key={v}
                                title={v}
                                sticky
                                items={open2 ? items : []}
                                count={items.length}
                                collapsible
                                expanded={open2}
                                onToggle={() =>
                                  setOpenVendor((m) => ({
                                    ...m,
                                    [key]: !open2,
                                  }))
                                }
                                activeId={active?.preset_label || ""}
                                isActiveProvider={
                                  active?.provider_id === g.provider.id
                                }
                                onPick={(lbl) => choose(g.provider.id, lbl)}
                                onPin={(lbl, pin) =>
                                  togglePin(g.provider.id, lbl, pin)
                                }
                              />
                            );
                          })}
                        </>
                      ) : (
                        <PresetList
                          items={g.sorted}
                          activeId={active?.preset_label || ""}
                          isActiveProvider={
                            active?.provider_id === g.provider.id
                          }
                          onPick={(lbl) => choose(g.provider.id, lbl)}
                          onPin={(lbl, pin) =>
                            togglePin(g.provider.id, lbl, pin)
                          }
                        />
                      )}
                    </div>
                  )}
                </div>
              );
            })}
            {groups.every((g) => !g.matched) && (
              <div
                className="muted"
                style={{ padding: 20, textAlign: "center", fontSize: 13 }}
              >
                没匹配的模型 — 试试别的关键字,或去 API 管理添加
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function VendorBlock({
  title,
  items,
  count,
  sticky,
  collapsible,
  expanded,
  onToggle,
  activeId,
  isActiveProvider,
  onPick,
  onPin,
}: {
  title: string;
  items: { label: string; model: string; pinned?: boolean; description?: string }[];
  count?: number;
  sticky?: boolean;
  collapsible?: boolean;
  expanded?: boolean;
  onToggle?: () => void;
  activeId: string;
  isActiveProvider: boolean;
  onPick: (label: string) => void;
  onPin: (label: string, pinned: boolean) => void;
}) {
  return (
    <div>
      <div
        onClick={collapsible ? onToggle : undefined}
        style={{
          position: sticky ? "sticky" : "static",
          top: sticky ? 33 : undefined,
          zIndex: 1,
          padding: "4px 10px 4px 26px",
          fontSize: 11,
          color: "var(--muted)",
          background: "var(--panel)",
          borderBottom: "1px solid var(--border-2)",
          cursor: collapsible ? "pointer" : undefined,
          display: "flex",
          alignItems: "center",
          gap: 4,
        }}
      >
        {collapsible && (
          <span style={{ opacity: 0.6, fontSize: 9 }}>
            {expanded ? "▾" : "▸"}
          </span>
        )}
        <span>{title}</span>
        {count != null && (
          <span style={{ marginLeft: "auto", opacity: 0.7 }}>{count}</span>
        )}
      </div>
      {items.length > 0 && (
        <PresetList
          items={items}
          activeId={activeId}
          isActiveProvider={isActiveProvider}
          onPick={onPick}
          onPin={onPin}
        />
      )}
    </div>
  );
}

function PresetList({
  items,
  activeId,
  isActiveProvider,
  onPick,
  onPin,
}: {
  items: { label: string; model: string; pinned?: boolean; description?: string }[];
  activeId: string;
  isActiveProvider: boolean;
  onPick: (label: string) => void;
  onPin: (label: string, pinned: boolean) => void;
}) {
  return (
    <div>
      {items.map((pr) => {
        const isAct = isActiveProvider && pr.label === activeId;
        return (
          <div
            key={pr.label}
            onClick={() => onPick(pr.label)}
            style={{
              padding: "6px 10px 6px 28px",
              cursor: "pointer",
              fontSize: 13,
              background: isAct ? "var(--accent-bg, rgba(47,158,68,.12))" : undefined,
              borderLeft: isAct ? "3px solid #2f9e44" : "3px solid transparent",
              display: "flex",
              alignItems: "center",
              gap: 6,
            }}
            onMouseEnter={(e) => {
              if (!isAct)
                (e.currentTarget as HTMLDivElement).style.background =
                  "var(--panel-2)";
            }}
            onMouseLeave={(e) => {
              if (!isAct)
                (e.currentTarget as HTMLDivElement).style.background = "";
            }}
          >
            <span
              role="button"
              tabIndex={0}
              onClick={(e) => {
                e.stopPropagation();
                onPin(pr.label, !pr.pinned);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  e.stopPropagation();
                  onPin(pr.label, !pr.pinned);
                }
              }}
              title={pr.pinned ? "取消置顶" : "置顶此模型"}
              style={{
                color: pr.pinned ? "#f59f00" : "var(--muted)",
                fontSize: 14,
                cursor: "pointer",
                userSelect: "none",
                padding: "0 4px",
                lineHeight: 1,
              }}
            >
              {pr.pinned ? "★" : "☆"}
            </span>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div
                style={{
                  fontWeight: isAct ? 600 : 400,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {pr.label}
              </div>
              {(pr.description || pr.model !== pr.label) && (
                <div
                  className="muted"
                  style={{
                    fontSize: 11,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                  title={`${pr.model}${
                    pr.description ? " — " + pr.description : ""
                  }`}
                >
                  {pr.description || pr.model}
                </div>
              )}
            </div>
            {isAct && (
              <span className="badge" style={{ fontSize: 10 }}>
                当前
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}
