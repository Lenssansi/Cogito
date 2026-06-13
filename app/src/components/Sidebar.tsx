// 左侧栏:顶部页签 + 会话历史(对话=Agent,单一列表)
import type { AgentSessionSummary, WhoAmI } from "../api";

export type Page = "chat" | "api" | "settings";

const NAV: { key: Page; label: string }[] = [
  { key: "chat", label: "对话" },
  { key: "api", label: "API 管理" },
  { key: "settings", label: "设置" },
];

export default function Sidebar({
  page,
  onPage,
  sessionList,
  activeSessionId,
  onSelectSession,
  onNewSession,
  onDeleteSession,
  backendOk,
  who,
  version,
}: {
  page: Page;
  onPage: (p: Page) => void;
  sessionList: AgentSessionSummary[];
  activeSessionId: string | null;
  onSelectSession: (id: string) => void;
  onNewSession: () => void;
  onDeleteSession: (id: string) => void;
  backendOk: boolean | null;
  who: WhoAmI | null;
  version: string;
}) {
  return (
    <nav className="sidebar">
      <div className="brand">
        <span className="brand-dot" />
        Cogito
      </div>
      <div className="nav-block">
        {NAV.map((n) => (
          <button
            key={n.key}
            className={"nav-item" + (page === n.key ? " active" : "")}
            onClick={() => onPage(n.key)}
          >
            {n.label}
          </button>
        ))}
      </div>

      {page === "chat" && <div className="sidebar-sep" />}

      {page === "chat" && (
        <div className="hist-block">
          <div className="hist-head">
            <span className="hist-title">会话历史</span>
            <button
              className="hist-new"
              onClick={onNewSession}
              title="开始新会话"
            >
              ＋
            </button>
          </div>
          <div className="hist-list">
            {sessionList.length ? (
              sessionList.map((s) => (
                <div
                  key={s.id}
                  className={
                    "hist-item" + (activeSessionId === s.id ? " active" : "")
                  }
                  onClick={() => onSelectSession(s.id)}
                >
                  <div className="hist-title-row">{s.title || "(无标题)"}</div>
                  {s.cwd && (
                    <div className="hist-sub" title={s.cwd}>
                      📁 {shortenPath(s.cwd)}
                    </div>
                  )}
                  <button
                    className="hist-del"
                    title="删除"
                    onClick={(e) => {
                      e.stopPropagation();
                      if (confirm(`删除「${s.title || s.id}」?`))
                        onDeleteSession(s.id);
                    }}
                  >
                    ×
                  </button>
                </div>
              ))
            ) : (
              <div className="hist-empty">还没有会话,「＋」开一个</div>
            )}
          </div>
        </div>
      )}

      <div className="sidebar-foot">
        <StatusLine ok={backendOk} who={who} version={version} />
      </div>
    </nav>
  );
}

function shortenPath(p: string): string {
  if (!p) return "";
  if (p.length <= 28) return p;
  const norm = p.replace(/\\/g, "/");
  const parts = norm.split("/");
  if (parts.length <= 2) return p.slice(0, 28) + "…";
  return parts[0] + "\\…\\" + parts[parts.length - 1];
}

function StatusLine({
  ok,
  who,
  version,
}: {
  ok: boolean | null;
  who: WhoAmI | null;
  version: string;
}) {
  if (ok === null) return <span className="status">连接后端中…</span>;
  if (!ok)
    return (
      <span className="status err">
        后端未连接
        <br />
        <span style={{ fontSize: 11 }}>先跑 start-dev.bat</span>
      </span>
    );
  return (
    <span className="status ok">
      已连 v{version}
      {who && (
        <>
          <br />
          {who.trust === "local" ? "本地全功能" : "远程受限"}
        </>
      )}
    </span>
  );
}
