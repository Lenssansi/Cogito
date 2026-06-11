import { useEffect, useState } from "react";
import ChatPage from "./pages/ChatPage";
import AgentPage from "./pages/AgentPage";
import ApiPage from "./pages/ApiPage";
import SettingsPage from "./pages/SettingsPage";
import Sidebar, { type Page } from "./components/Sidebar";
import {
  deleteAgentSession,
  deleteConversation,
  getHealth,
  getTheme,
  getWhoAmI,
  listAgentSessions,
  listConversations,
  type AgentSessionSummary,
  type ConvSummary,
  type ThemeMode,
  type WhoAmI,
} from "./api";

declare global {
  interface Window {
    aihelper?: {
      isElectron?: boolean;
      setNativeTheme?: (t: string) => void;
      pickFolder?: () => Promise<string>;
    };
  }
}

function effective(theme: ThemeMode): "dark" | "light" {
  if (theme === "system")
    return window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light";
  return theme;
}

export function applyTheme(theme: ThemeMode) {
  document.documentElement.dataset.theme = effective(theme);
  window.aihelper?.setNativeTheme?.(theme);
}

export default function App() {
  const [page, setPage] = useState<Page>("chat");
  const [backendOk, setBackendOk] = useState<boolean | null>(null);
  const [who, setWho] = useState<WhoAmI | null>(null);
  const [version, setVersion] = useState("");
  const [theme, setThemeState] = useState<ThemeMode>("dark");

  // 历史列表(全局):Sidebar 和 Page 共用一份;Page 在持久化后通知 App 刷新
  const [convList, setConvList] = useState<ConvSummary[]>([]);
  const [activeConvId, setActiveConvId] = useState<string | null>(null);
  const [sessionList, setSessionList] = useState<AgentSessionSummary[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);

  async function refreshConvList() {
    try {
      setConvList(await listConversations());
    } catch {
      /* ignore */
    }
  }
  async function refreshSessionList() {
    try {
      setSessionList(await listAgentSessions());
    } catch {
      /* ignore */
    }
  }

  useEffect(() => {
    (async () => {
      try {
        const h = await getHealth();
        setBackendOk(true);
        setVersion(h.version);
        setWho(await getWhoAmI());
        const t = (await getTheme()).theme;
        setThemeState(t);
        applyTheme(t);
        if (t === "system") setTimeout(() => applyTheme("system"), 80);
      } catch {
        setBackendOk(false);
      }
      refreshConvList();
      refreshSessionList();
    })();
  }, []);

  useEffect(() => {
    if (theme !== "system") return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const fn = () => applyTheme("system");
    mq.addEventListener("change", fn);
    return () => mq.removeEventListener("change", fn);
  }, [theme]);

  async function onDeleteConv(id: string) {
    try {
      await deleteConversation(id);
    } catch {
      /* ignore */
    }
    if (activeConvId === id) setActiveConvId(null);
    refreshConvList();
  }
  async function onDeleteSession(id: string) {
    try {
      await deleteAgentSession(id);
    } catch {
      /* ignore */
    }
    if (activeSessionId === id) setActiveSessionId(null);
    refreshSessionList();
  }

  return (
    <div className="app">
      <Sidebar
        page={page}
        onPage={setPage}
        convList={convList}
        activeConvId={activeConvId}
        onSelectConv={(id) => {
          setPage("chat");
          setActiveConvId(id);
        }}
        onNewConv={() => {
          setPage("chat");
          setActiveConvId(null);
        }}
        onDeleteConv={onDeleteConv}
        sessionList={sessionList}
        activeSessionId={activeSessionId}
        onSelectSession={(id) => {
          setPage("agent");
          setActiveSessionId(id);
        }}
        onNewSession={() => {
          setPage("agent");
          setActiveSessionId(null);
        }}
        onDeleteSession={onDeleteSession}
        backendOk={backendOk}
        who={who}
        version={version}
      />
      <main className="content">
        {page === "chat" && (
          <ChatPage
            activeConvId={activeConvId}
            onConvChange={setActiveConvId}
            onListUpdate={refreshConvList}
          />
        )}
        {page === "agent" && (
          <AgentPage
            who={who}
            activeSessionId={activeSessionId}
            onSessionChange={setActiveSessionId}
            onListUpdate={refreshSessionList}
          />
        )}
        {page === "api" && <ApiPage who={who} />}
        {page === "settings" && (
          <SettingsPage
            who={who}
            theme={theme}
            onTheme={(t) => {
              setThemeState(t);
              applyTheme(t);
            }}
          />
        )}
      </main>
    </div>
  );
}
