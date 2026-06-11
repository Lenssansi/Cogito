// 左侧栏:顶部页签 + 下方按当前页的历史列表(对话/编程会话)
// 设计参考 Claude Desktop —— Tab 切换 + 当前 Tab 对应历史下方展示
import type { AgentSessionSummary, ConvSummary, WhoAmI } from "../api";

export type Page = "chat" | "agent" | "api" | "settings";

const NAV: { key: Page; label: string }[] = [
  { key: "chat", label: "对话" },
  { key: "agent", label: "编程" },
  { key: "api", label: "API 管理" },
  { key: "settings", label: "设置" },
];

export default function Sidebar({
  page,
  onPage,
  convList,
  activeConvId,
  onSelectConv,
  onNewConv,
  onDeleteConv,
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
  convList: ConvSummary[];
  activeConvId: string | null;
  onSelectConv: (id: string) => void;
  onNewConv: () => void;
  onDeleteConv: (id: string) => void;
  sessionList: AgentSessionSummary[];
  activeSessionId: string | null;
  onSelectSession: (id: string) => void;
  onNewSession: () => void;
  onDeleteSession: (id: string) => void;
  backendOk: boolean | null;
  who: WhoAmI | null;
  version: string;
}) {
  const showHistory = page === "chat" || page === "agent";

  return (
    <nav className="sidebar">
      <div className="brand">
        <span className="brand-dot" />
        ai-helper
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

      {showHistory && <div className="sidebar-sep" />}

      {page === "chat" && (
        <div className="hist-block">
          <div className="hist-head">
            <span className="hist-title">对话历史</span>
            <button
              className="hist-new"
              onClick={onNewConv}
              title="开始新对话"
            >
              ＋
            </button>
          </div>
          <div className="hist-list">
            {convList.length ? (
              convList.map((c) => (
                <div
                  key={c.id}
                  className={
                    "hist-item" + (activeConvId === c.id ? " active" : "")
                  }
                  onClick={() => onSelectConv(c.id)}
                >
                  <div className="hist-title-row">{c.title || "(无标题)"}</div>
                  <button
                    className="hist-del"
                    title="删除"
                    onClick={(e) => {
                      e.stopPropagation();
                      if (confirm(`删除「${c.title || c.id}」?`))
                        onDeleteConv(c.id);
                    }}
                  >
                    ×
                  </button>
                </div>
              ))
            ) : (
              <div className="hist-empty">还没有对话,「＋」开一个</div>
            )}
          </div>
        </div>
      )}

      {page === "agent" && (
        <div className="hist-block">
          <div className="hist-head">
            <span className="hist-title">编程会话</span>
            <button
              className="hist-new"
              onClick={onNewSession}
              title="开始新编程会话"
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
  // D:\foo\bar\very\deep\proj → D:\…\proj 当太长
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
        <span style={{ fontSize: 11 }}>先跑 开发启动.bat</span>
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
