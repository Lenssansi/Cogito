import { useEffect, useRef, useState } from "react";
import {
  agentRollback,
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

export default function AgentPage({
  who,
  activeSessionId,
  onSessionChange,
  onListUpdate,
}: {
  who: WhoAmI | null;
  // 父级(App)控制当前打开的编程会话 id
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
  const acRef = useRef<AbortController | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const runIdRef = useRef("");

  useEffect(() => {
    (async () => {
      getProviders().then(setPs).catch(() => void 0);
      try {
        const w = await getWorkspace();
        setWs(w);
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
      setWebOn(s.web_on !== false); // 默认开
      // 从 transcript 抽最后一次 todos 事件还原右侧面板
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

  const activeProv =
    ps?.providers.find((p) => p.id === ps.active.provider_id) || null;
  void activeProv;

  async function pickCwd(dir: string) {
    setWs(await saveWorkspace({ cwd: dir }));
    // 该目录有历史会话→跳到最近一个;没有→新会话(父级状态驱动)
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
  }, [events]);

  function onEvent(e: AgentEvent) {
    if (e.type === "run" && e.run_id) {
      runIdRef.current = e.run_id;
      setRunId(e.run_id);
      onSessionChange(e.run_id); // 新会话生成 → 通知父级更新 Sidebar 选中
    }
    if (e.type === "todos" && e.items) {
      setTodos(e.items);
    }
    if (e.type === "confirm") {
      setStatus("awaiting");
      setEditArgs(JSON.stringify(e.args ?? {}, null, 2));
    }
    if (e.type === "done") setStatus("done");
    if (e.type === "error") setStatus("error");
    setEvents((p) => [...p, e]);
  }

  function submit() {
    const t = task.trim();
    if (!t || status === "running" || status === "awaiting") return;
    setTask("");
    const hasRun = !!runIdRef.current;
    // 不本地插 user 事件——后端 start/continue 都会推送并写入 transcript
    setStatus("running");
    const path = hasRun ? "/api/agent/continue" : "/api/agent/start";
    const body = hasRun
      ? { run_id: runIdRef.current, task: t, web: webOn }
      : { task: t, web: webOn };
    acRef.current = streamSSE(path, body, onEvent, () => {
      setStatus((s) => (s === "running" ? "idle" : s));
      onListUpdate();
    });
  }

  function newSession() {
    acRef.current?.abort();
    runIdRef.current = "";
    setRunId("");
    setEvents([]);
    setStatus("idle");
    setTodos([]);
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

  async function doRollback() {
    if (!runIdRef.current) return;
    if (
      !confirm(
        "确认回滚？将 git reset --hard 到本次任务开始的检查点，" +
          "工作目录未提交的改动会丢失。"
      )
    )
      return;
    const r = await agentRollback(runIdRef.current);
    alert(r.error ? "回滚失败：" + r.error : "已回滚到检查点");
  }

  const noWs =
    !ws || !ws.allowed_roots.length || !ws.cwd;

  return (
    <div className="page chat">
      <div className="chat-head">
        <h1>编程 Agent</h1>
        <div className="chat-tools">
          <select
            title="工作目录（白名单内）"
            value={ws?.cwd ?? ""}
            onChange={(e) => pickCwd(e.target.value)}
          >
            {(ws?.allowed_roots ?? []).map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
          {ws && !ws.cwd_is_git && (
            <button className="danger" onClick={initGit} title="该目录不是 git 仓库">
              初始化 git
            </button>
          )}
          <ModelSwitcher
            ps={ps}
            compact
            onChange={async () => {
              const fresh = await getProviders();
              setPs(fresh);
            }}
          />
          <button
            className={"cfg-toggle" + (webOn ? " on" : "")}
            onClick={() => setWebOn((v) => !v)}
            title="允许 Agent 调 web_search 工具查在线资料(智能联网由模型决定何时搜)"
          >
            🌐 联网{webOn ? "·开" : "·关"}
          </button>
          {runId && (
            <>
              <button className="danger" onClick={doRollback}>
                回滚检查点
              </button>
            </>
          )}
        </div>
      </div>

      {!canAgent && (
        <div className="cfg-note warn">远程访问下 Agent 不可用。</div>
      )}
      {noWs && (
        <div className="placeholder warn">
          还没配置工作区。去「设置」页：添加授权根目录 + 选当前工作目录
          （须是 git 仓库）+ 可选填测试命令。
        </div>
      )}

      <div className="agent-body">
      <div className="msgs">
        {initLoading && (
          <div className="loading-row">
            <span className="spinner" />
            <span>加载会话中…</span>
          </div>
        )}
        {events.map((e, i) => (
          <EventRow key={i} e={e} />
        ))}
        {status === "awaiting" && (
          <div className="confirm-box">
            <div className="confirm-title">
              ⚠️ 高危操作待确认：<b>{events[events.length - 1]?.tool}</b>
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
        )}
        <div ref={endRef} />
      </div>
      {todos.length > 0 && <TodoPanel todos={todos} />}
      </div>

      <div className="composer">
        <textarea
          value={task}
          disabled={!canAgent || noWs || status === "running"}
          placeholder="用自然语言描述编程任务，如：给 utils.py 的 parse() 加上空输入处理并跑测试"
          onChange={(e) => setTask(e.target.value)}
        />
        <button
          onClick={submit}
          disabled={
            !canAgent ||
            noWs ||
            status === "running" ||
            status === "awaiting"
          }
        >
          {status === "running"
            ? "运行中…"
            : runId
            ? "继续"
            : "开始"}
        </button>
      </div>
    </div>
  );
}

function EventRow({ e }: { e: AgentEvent }) {
  if (e.type === "user")
    return (
      <div className="ev ev-user">
        <span className="role">你</span>
        <span style={{ whiteSpace: "pre-wrap" }}>{e.content}</span>
      </div>
    );
  if (e.type === "checkpoint")
    return (
      <div className="ev ev-cp">
        🛟 已打 git 检查点 {String(e.commit).slice(0, 8)}（可随时回滚）
      </div>
    );
  if (e.type === "tool")
    return (
      <div className="ev ev-tool">
        ▶ 调用 <b>{e.name}</b>
        <pre>{JSON.stringify(e.args, null, 2)}</pre>
      </div>
    );
  if (e.type === "result") {
    const r = e.result as Record<string, unknown>;
    const err = r && (r as { error?: string }).error;
    return (
      <div className={"ev ev-res" + (err ? " err" : "")}>
        {err ? "✖ " : "✓ "}
        {e.name}
        <pre>{JSON.stringify(e.result, null, 2).slice(0, 4000)}</pre>
      </div>
    );
  }
  if (e.type === "answer")
    return (
      <div className="ev ev-ans">
        <Markdown text={e.content || ""} />
      </div>
    );
  if (e.type === "error")
    return <div className="ev ev-err">出错：{e.error}</div>;
  if (e.type === "done") return <div className="ev ev-done">— 任务结束 —</div>;
  if (e.type === "info")
    return <div className="ev ev-info">{e.content}</div>;
  // todos 事件不直接渲染到主流;走右侧面板
  return null;
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
