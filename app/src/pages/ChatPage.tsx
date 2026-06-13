// 统一会话页(对话 = Agent,Claude Code 式):
// 以根目录为工作基地,工具常驻,纯聊天 = 零工具轮;读全盘自由,根外写
// 操作后端会弹确认;根目录是 git 仓库则自动有检查点/回滚。
// delta/reasoning 是流式瞬时事件(打字机),answer 是最终落 transcript 的回答。
import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  agentRollback,
  agentStop,
  getAgentSession,
  getProviders,
  getWorkspace,
  gitInitWorkspace,
  listAgentSessions,
  saveWorkspace,
  streamSSE,
  type AgentEvent,
  type ProvidersState,
  type TodoItem,
  type WhoAmI,
  type WorkspaceCfg,
} from "../api";
import Markdown from "../components/Markdown";
import ModelSwitcher from "../components/ModelSwitcher";

type Status = "idle" | "running" | "awaiting" | "done" | "error";

export default function ChatPage({
  who,
  activeSessionId,
  onSessionChange,
  onListUpdate,
}: {
  who: WhoAmI | null;
  activeSessionId: string | null;
  onSessionChange: (id: string | null) => void;
  onListUpdate: () => void;
}) {
  const canAgent = who ? who.permissions.agent !== false : true;
  const [ws, setWs] = useState<WorkspaceCfg | null>(null);
  const [ps, setPs] = useState<ProvidersState | null>(null);
  const [task, setTask] = useState("");
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [status, setStatus] = useState<Status>("idle");
  const [runId, setRunId] = useState("");
  const [editArgs, setEditArgs] = useState("");
  const [webOn, setWebOn] = useState(true);
  const [initLoading, setInitLoading] = useState(true);
  const [todos, setTodos] = useState<TodoItem[]>([]);
  // 流式瞬时缓冲(不进 events;answer 事件到达时清空)
  const [liveText, setLiveText] = useState("");
  const [liveThink, setLiveThink] = useState("");
  const acRef = useRef<AbortController | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const runIdRef = useRef("");

  useEffect(() => {
    (async () => {
      getProviders().then(setPs).catch(() => void 0);
      try {
        setWs(await getWorkspace());
      } catch {
        /* ignore */
      }
      setInitLoading(false);
    })();
  }, []);

  // 跟随父级 activeSessionId:null=新会话;非空且不同=载入
  useEffect(() => {
    if (activeSessionId === null) {
      newSession();
    } else if (activeSessionId !== runIdRef.current) {
      loadSession(activeSessionId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeSessionId]);

  async function loadSession(id: string) {
    try {
      const s = await getAgentSession(id);
      runIdRef.current = s.id;
      setRunId(s.id);
      setEvents(s.transcript || []);
      setStatus(s.status === "awaiting" ? "awaiting" : "done");
      setWebOn(s.web_on !== false);
      setLiveText("");
      setLiveThink("");
      const lastTodos = [...(s.transcript || [])]
        .reverse()
        .find((ev) => ev.type === "todos");
      setTodos(lastTodos?.items || []);
      const last = (s.transcript || [])[s.transcript.length - 1];
      if (s.status === "awaiting" && last?.args)
        setEditArgs(JSON.stringify(last.args, null, 2));
    } catch {
      /* ignore */
    }
  }

  async function pickCwd(dir: string) {
    setWs(await saveWorkspace({ cwd: dir }));
    // 该目录有历史会话→跳到最近一个;没有→新会话
    const list = await listAgentSessions();
    onListUpdate();
    const mine = list.filter((s) => s.cwd === dir);
    if (mine.length) onSessionChange(mine[0].id);
    else onSessionChange(null);
  }

  async function initGit() {
    if (!ws?.cwd) return;
    try {
      await gitInitWorkspace(ws.cwd);
      setWs(await getWorkspace());
    } catch (e) {
      alert((e as Error).message);
    }
  }

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [events, liveText, liveThink]);

  function onEvent(e: AgentEvent) {
    // 流式瞬时事件:进缓冲不进 events
    if (e.type === "delta") {
      setLiveText((t) => t + (e.content || ""));
      return;
    }
    if (e.type === "reasoning") {
      setLiveThink((t) => t + (e.content || ""));
      return;
    }
    // 最终回答/中止到达 → 清流式缓冲(answer 行会接管显示)
    if (["answer", "done", "error", "confirm", "cancelled"].includes(e.type)) {
      setLiveText("");
      setLiveThink("");
    }
    if (e.type === "run" && e.run_id) {
      runIdRef.current = e.run_id;
      setRunId(e.run_id);
      onSessionChange(e.run_id);
    }
    if (e.type === "todos" && e.items) setTodos(e.items);
    if (e.type === "confirm") {
      setStatus("awaiting");
      setEditArgs(JSON.stringify(e.args ?? {}, null, 2));
    }
    if (e.type === "done") setStatus("done");
    if (e.type === "cancelled") setStatus("done");
    if (e.type === "error") setStatus("error");
    setEvents((p) => [...p, e]);
  }

  function submit() {
    const t = task.trim();
    if (!t || status === "running" || status === "awaiting") return;
    setTask("");
    const hasRun = !!runIdRef.current;
    setStatus("running");
    const path = hasRun ? "/api/agent/continue" : "/api/agent/start";
    const body = hasRun
      ? { run_id: runIdRef.current, task: t, web: webOn }
      : { task: t, web: webOn };
    acRef.current = streamSSE(path, body, onEvent, () => {
      setStatus((s) => (s === "running" ? "idle" : s));
      setLiveText("");
      setLiveThink("");
      onListUpdate();
    });
  }

  function stop() {
    // 先通知后端(杀子进程+标 cancelled),再断流
    const rid = runIdRef.current;
    if (rid) agentStop(rid).catch(() => void 0);
    acRef.current?.abort();
    setStatus("idle");
    setLiveText("");
    setLiveThink("");
  }

  function newSession() {
    acRef.current?.abort();
    runIdRef.current = "";
    setRunId("");
    setEvents([]);
    setStatus("idle");
    setTodos([]);
    setLiveText("");
    setLiveThink("");
  }

  function respond(approve: boolean) {
    let ea: unknown = undefined;
    if (approve && editArgs.trim()) {
      try {
        ea = JSON.parse(editArgs);
      } catch {
        alert("编辑后的参数不是合法 JSON");
        return;
      }
    }
    setStatus("running");
    acRef.current = streamSSE(
      "/api/agent/respond",
      { run_id: runIdRef.current, approve, edited_args: ea },
      onEvent,
      () => {
        setStatus((s) => (s === "running" ? "idle" : s));
        onListUpdate();
      }
    );
  }

  async function doRollback(to?: string) {
    if (!runIdRef.current) return;
    const msg = to
      ? "回滚到这个检查点？会 git reset --hard 到此处——这之后(含本轮)" +
        "对根目录的改动都会被丢弃。根目录外的文件不受影响。"
      : "回滚到最早的检查点？会 git reset --hard 撤掉本会话对根目录的" +
        "所有改动。根目录外的文件不受影响。";
    if (!confirm(msg)) return;
    const r = await agentRollback(runIdRef.current, to);
    if (r.error) alert("回滚失败：" + r.error);
    else alert("已回滚到检查点 " + (r.rolled_back_to || "").slice(0, 8));
  }

  const noWs = !ws || !ws.cwd;
  const hasCheckpoint = events.some((e) => e.type === "checkpoint");
  const lastEv = events[events.length - 1];
  const toolRunning =
    status === "running" && lastEv?.type === "tool";

  return (
    <div className="page chat">
      <div className="chat-head">
        <h1>对话</h1>
        <div className="chat-tools">
          <select
            title="根目录(会话的工作基地;读全盘自由,根外写操作会要确认)"
            value={ws?.cwd ?? ""}
            onChange={(e) => pickCwd(e.target.value)}
          >
            {(ws?.allowed_roots ?? []).map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
          {ws && ws.cwd && !ws.cwd_is_git && (
            <button
              className="cfg-toggle"
              onClick={initGit}
              title="根目录不是 git 仓库:会话没有检查点/回滚安全网。点击初始化"
            >
              ⚠ 无检查点
            </button>
          )}
          <ModelSwitcher
            ps={ps}
            compact
            onChange={async () => {
              setPs(await getProviders());
            }}
          />
          <button
            className={"cfg-toggle" + (webOn ? " on" : "")}
            onClick={() => setWebOn((v) => !v)}
            title="允许调 web_search 工具查在线资料(何时搜由模型决定)"
          >
            🌐 联网{webOn ? "·开" : "·关"}
          </button>
          {runId && hasCheckpoint && (
            <button
              className="danger"
              onClick={() => doRollback()}
              title="回到最早的检查点:撤掉本会话对根目录的所有改动"
            >
              全部回滚
            </button>
          )}
        </div>
      </div>

      {!canAgent && (
        <div className="cfg-note warn">远程访问下会话不可用(仅本机)。</div>
      )}
      {noWs && (
        <div className="placeholder warn">
          还没设根目录。去「设置」页添加授权根目录并选当前工作目录
          ——它是会话的工作基地(不强制 git;有 git 才有检查点安全网)。
        </div>
      )}

      <div className="agent-body">
        <div className="chat-thread">
          {initLoading && (
            <div className="loading-row">
              <span className="spinner" />
              <span>加载会话中…</span>
            </div>
          )}
          {!initLoading && events.length === 0 && (
            <div className="empty">
              聊天、读写文件、跑命令、写代码都行——纯聊天直接答,干活自动用
              工具。涉及桌面/下载等其他位置直接说;根目录外的改动会先问你。
            </div>
          )}
          {buildTurns(events).map((t, i) =>
            t.kind === "me" ? (
              <div key={i} className="turn me">
                <div className="avatar">我</div>
                <div className="bubble">{t.content}</div>
              </div>
            ) : (
              <AiTurn
                key={i}
                evs={t.evs}
                live={t.last ? { text: liveText, think: liveThink } : null}
                running={!!t.last && status === "running"}
                toolRunning={!!t.last && toolRunning}
                lastToolName={lastEv?.name}
                onRollback={doRollback}
              />
            )
          )}
          {status === "awaiting" && (
            <div className="turn ai">
              <div className="avatar">AI</div>
              <div className="confirm-box" style={{ flex: 1 }}>
                <div className="confirm-title">
                  ⚠️ 操作待确认：<b>{events[events.length - 1]?.tool}</b>
                  (可改参数 JSON)
                </div>
                <textarea
                  className="sys-area"
                  value={editArgs}
                  onChange={(e) => setEditArgs(e.target.value)}
                />
                <div className="cfg-actions">
                  <button onClick={() => respond(true)}>批准并执行</button>
                  <button className="danger" onClick={() => respond(false)}>
                    拒绝
                  </button>
                </div>
              </div>
            </div>
          )}
          <div ref={endRef} />
        </div>
        {todos.length > 0 && <TodoPanel todos={todos} />}
      </div>

      <div className="composer">
        <textarea
          value={task}
          disabled={!canAgent || noWs || status === "running"}
          placeholder="聊天或下任务都行,Enter 发送,Shift+Enter 换行"
          onChange={(e) => setTask(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
        />
        {status === "running" ? (
          <button className="stop" onClick={stop}>
            停止
          </button>
        ) : (
          <button
            onClick={submit}
            disabled={!canAgent || noWs || status === "awaiting"}
          >
            {runId ? "发送" : "开始"}
          </button>
        )}
      </div>
    </div>
  );
}

// 把扁平事件流按「用户一条 → AI 一轮」分组成聊天气泡。
// delta/reasoning 是瞬时流(不在 events 里),这里只处理落库事件。
type Turn =
  | { kind: "me"; content: string; last?: boolean }
  | { kind: "ai"; evs: AgentEvent[]; last?: boolean };

function buildTurns(events: AgentEvent[]): Turn[] {
  const turns: Turn[] = [];
  let ai: Extract<Turn, { kind: "ai" }> | null = null;
  for (const e of events) {
    if (e.type === "delta" || e.type === "reasoning") continue;
    if (e.type === "user") {
      turns.push({ kind: "me", content: e.content || "" });
      ai = { kind: "ai", evs: [] };
      turns.push(ai);
    } else {
      if (!ai) {
        ai = { kind: "ai", evs: [] };
        turns.push(ai);
      }
      ai.evs.push(e);
    }
  }
  // 标记最后一个 AI 轮:它来挂流式缓冲 + 心跳 spinner
  for (let i = turns.length - 1; i >= 0; i--) {
    if (turns[i].kind === "ai") {
      turns[i].last = true;
      break;
    }
  }
  return turns;
}

function argPreview(args?: Record<string, unknown>): string {
  if (!args) return "";
  const p = args.path as string | undefined;
  if (p) return p.split(/[\\/]/).pop() || "";
  if (typeof args.command === "string") return args.command.slice(0, 48);
  if (typeof args.query === "string") return args.query.slice(0, 32);
  if (typeof args.pattern === "string") return args.pattern.slice(0, 32);
  return "";
}

// 一次工具调用 = 折叠卡片(摘要一行,点开看参数/结果)。
function ToolChip({
  tool,
  result,
}: {
  tool: AgentEvent | null;
  result: AgentEvent | null;
}) {
  const name = tool?.name || result?.name || "工具";
  const err = !!(result?.result as { error?: string } | undefined)?.error;
  const running = !!tool && !result;
  const preview = argPreview(tool?.args);
  return (
    <details className={"tool-chip" + (err ? " err" : "")}>
      <summary>
        {running ? (
          <span className="spinner spin-dot" />
        ) : (
          <span>{err ? "✖" : "✓"}</span>
        )}
        <span>
          <b>{name}</b>
          {preview && <span className="muted"> {preview}</span>}
          {running && <span className="muted"> · 执行中…</span>}
        </span>
      </summary>
      <pre>
        {tool ? "参数 " + JSON.stringify(tool.args) + "\n" : ""}
        {result
          ? "结果 " + JSON.stringify(result.result).slice(0, 3000)
          : running
            ? "(执行中…)"
            : ""}
      </pre>
    </details>
  );
}

// AI 的一轮:气泡里依次是检查点标记 / 折叠工具 / 思考 / 回答。
function AiTurn({
  evs,
  live,
  running,
  toolRunning,
  lastToolName,
  onRollback,
}: {
  evs: AgentEvent[];
  live: { text: string; think: string } | null;
  running: boolean;
  toolRunning: boolean;
  lastToolName?: string;
  onRollback: (commit: string) => void;
}) {
  const nodes: ReactNode[] = [];
  for (let i = 0; i < evs.length; i++) {
    const e = evs[i];
    if (e.type === "tool") {
      const next = evs[i + 1];
      const res = next && next.type === "result" ? next : null;
      if (res) i++;
      nodes.push(<ToolChip key={i} tool={e} result={res} />);
    } else if (e.type === "result") {
      nodes.push(<ToolChip key={i} tool={null} result={e} />);
    } else if (e.type === "checkpoint") {
      const commit = String(e.commit);
      nodes.push(
        <div key={i} className="cp-line">
          🛟 检查点 {commit.slice(0, 8)}
          <button
            onClick={() => onRollback(commit)}
            title="git reset --hard 回到此检查点(撤掉这一轮及之后的改动)"
          >
            回滚到这里
          </button>
        </div>
      );
    } else if (e.type === "info") {
      nodes.push(
        <div key={i} className="muted" style={{ fontSize: 12 }}>
          {e.content}
        </div>
      );
    } else if (e.type === "answer") {
      nodes.push(<Markdown key={i} text={e.content || ""} />);
    } else if (e.type === "error") {
      nodes.push(
        <div key={i} style={{ color: "var(--err)" }}>
          ⚠️ {e.error}
        </div>
      );
    } else if (e.type === "cancelled") {
      nodes.push(
        <div key={i} className="muted" style={{ fontSize: 12 }}>
          — 已停止 —
        </div>
      );
    }
    // done / todos / confirm:不在气泡里渲染
  }
  if (live?.think)
    nodes.push(
      <details key="lt" className="think bubble-think" open={!live.text}>
        <summary>💭 思考过程</summary>
        <div className="think-body">{live.think}</div>
      </details>
    );
  if (live?.text) nodes.push(<Markdown key="ld" text={live.text} live />);

  const showSpin = running && !live?.text && !live?.think;
  if (nodes.length === 0 && !showSpin) return null;

  return (
    <div className="turn ai">
      <div className="avatar">AI</div>
      <div className="bubble">
        {nodes}
        {showSpin && (
          <div
            className="muted"
            style={{
              display: "flex",
              alignItems: "center",
              gap: 6,
              fontSize: 12,
            }}
          >
            <span
              className="spinner"
              style={{ width: 11, height: 11, borderWidth: 2 }}
            />
            {toolRunning
              ? lastToolName === "run_command"
                ? "命令运行中,可能需要一会儿…"
                : "工具执行中…"
              : "思考中…"}
          </div>
        )}
      </div>
    </div>
  );
}

function TodoPanel({ todos }: { todos: TodoItem[] }) {
  const total = todos.length;
  const done = todos.filter((t) => t.status === "completed").length;
  const active = todos.filter((t) => t.status === "in_progress").length;
  const progress = total > 0 ? done / total : 0;
  return (
    <aside className="todo-panel">
      <div className="todo-head">
        <Ring progress={progress} />
        <div className="todo-stats">
          <div className="todo-stats-main">
            {done} / {total}
          </div>
          <div className="todo-stats-sub">
            {active > 0 && <span className="todo-pill doing">进行 {active}</span>}
            <span className="todo-pill pending">
              待办 {total - done - active}
            </span>
          </div>
        </div>
      </div>
      <div className="todo-list">
        {todos.map((t) => (
          <div key={t.id} className={"todo-item s-" + t.status}>
            <span className="todo-mark" aria-hidden="true">
              {t.status === "completed"
                ? "✓"
                : t.status === "in_progress"
                ? "●"
                : "○"}
            </span>
            <span className="todo-title">{t.title}</span>
          </div>
        ))}
      </div>
    </aside>
  );
}

function Ring({ progress }: { progress: number }) {
  const r = 26;
  const c = 2 * Math.PI * r;
  const p = Math.max(0, Math.min(1, progress));
  const off = c * (1 - p);
  return (
    <svg width="68" height="68" viewBox="0 0 68 68" className="todo-ring">
      <circle
        cx="34"
        cy="34"
        r={r}
        fill="none"
        stroke="var(--border-2)"
        strokeWidth="5"
      />
      <circle
        cx="34"
        cy="34"
        r={r}
        fill="none"
        stroke="var(--accent)"
        strokeWidth="5"
        strokeDasharray={c}
        strokeDashoffset={off}
        transform="rotate(-90 34 34)"
        strokeLinecap="round"
      />
      <text
        x="34"
        y="39"
        textAnchor="middle"
        fontSize="14"
        fontWeight="600"
        fill="var(--text)"
      >
        {Math.round(p * 100)}%
      </text>
    </svg>
  );
}
