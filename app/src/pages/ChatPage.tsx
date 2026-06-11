import { useEffect, useRef, useState } from "react";
import {
  chatfsStop,
  createConversation,
  getConversation,
  getProviders,
  getWorkspace,
  saveConversation,
  streamChat,
  streamSSE,
  type AgentEvent,
  type ChatMsg,
  type ProvidersState,
} from "../api";
import Markdown from "../components/Markdown";
import ModelSwitcher from "../components/ModelSwitcher";

// 智能路由:判断用户消息是否「明显涉及文件/目录操作」。命中=走文件工具
// 循环(可读写本机文件,失去 token 流式);否则=走流式 streamChat(保留
// token-by-token,无文件能力)。宁可漏判(可重述),不要误判(失去流式)。
const _FILE_INTENT: RegExp[] = [
  /[A-Za-z]:[\\/]/, // Windows 绝对路径 C:\ D:/
  /(^|[\s/])~\//, // ~ 主目录
  // 文件扩展名(粗筛常见的)
  /\.(bat|cmd|exe|ps1|sh|py|pyw|ipynb|js|ts|tsx|jsx|cjs|mjs|json|md|txt|csv|tsv|log|ya?ml|ini|cfg|conf|html?|css|scss|sass|sql|xml|toml|rs|go|java|c|cpp|h|hpp|kt|dart|lua|rb|php|vue|svelte|astro|zip|tar|gz|rar|7z|pdf|docx?|xlsx?)\b/i,
  // 中文动作动词 + 文件/目录/脚本类宾语
  /(读取?|读一?下|读这|打开|创建|新建|新增|写入?|写一?下|编辑|改一?下|修改|删除|删掉|清空|查看|看一?下|看看(这|那)?|列出?|遍历|搜索|搜一?下|找一?下|找出|查一?下|查找|检索|分析|定位|检查|修复|运行|执行|跑一?下|跑个|启动|测试一?下)[一]?[下个的]?(文件|目录|文件夹|脚本|代码|配置|日志|数据|项目|程序|工程)/,
  /(桌面|下载|文档目录|主目录|工作目录)/,
  // 英文动词 + 名词
  /\b(read|write|edit|create|delete|open|list|search|find|fix|run|execute|grep|cat|ls|tail|head)\b[^.\n]*\b(file|files|dir|directory|folder|script|code|repo|project)\b/i,
];

function looksLikeFileTask(text: string): boolean {
  if (!text) return false;
  return _FILE_INTENT.some((re) => re.test(text));
}

export default function ChatPage({
  activeConvId,
  onConvChange,
  onListUpdate,
}: {
  // 父级(App)控制当前打开的对话 id;Sidebar 切换历史时由父更新
  activeConvId: string | null;
  onConvChange: (id: string | null) => void;
  onListUpdate: () => void;
}) {
  const [ps, setPs] = useState<ProvidersState | null>(null);
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [initLoading, setInitLoading] = useState(true);
  const [webOn, setWebOn] = useState(false);
  // 文件工具默认始终可用(不再设 toggle);设置工具(改设置)从对话中移除
  const [cfAwait, setCfAwait] = useState<AgentEvent | null>(null);
  const [editArgs, setEditArgs] = useState("");

  const acRef = useRef<AbortController | null>(null);
  const msgsRef = useRef<HTMLDivElement>(null);
  const convIdRef = useRef<string | null>(null);
  const messagesRef = useRef<ChatMsg[]>([]);
  const jumpBottomRef = useRef(false);
  const webOnRef = useRef(false);
  const baseDirRef = useRef("");
  const cfRunRef = useRef("");
  const cfAwaitRef = useRef(false);

  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);
  useEffect(() => {
    webOnRef.current = webOn;
  }, [webOn]);
  useEffect(() => {
    cfAwaitRef.current = !!cfAwait;
  }, [cfAwait]);
  useEffect(() => {
    getWorkspace()
      .then((w) => (baseDirRef.current = w.cwd || ""))
      .catch(() => void 0);
  }, []);

  useEffect(() => {
    (async () => {
      try {
        getProviders().then(setPs).catch(() => void 0);
      } catch {
        /* ignore */
      } finally {
        setInitLoading(false);
      }
    })();
  }, []);

  // 跟随父级 activeConvId:null=新对话;非空且与内部 convId 不一致=载入
  useEffect(() => {
    if (activeConvId === null) {
      newChat();
    } else if (activeConvId !== convIdRef.current) {
      loadConv(activeConvId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeConvId]);

  useEffect(() => {
    const el = msgsRef.current;
    if (!el) return;
    if (jumpBottomRef.current) {
      el.scrollTop = el.scrollHeight;
      jumpBottomRef.current = false;
      return;
    }
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 140)
      el.scrollTop = el.scrollHeight;
  }, [messages]);

  const activeProv =
    ps?.providers.find((p) => p.id === ps.active.provider_id) || null;

  function persistNow(msgs: ChatMsg[]) {
    const id = convIdRef.current;
    if (!id || !msgs.length) return;
    const title =
      msgs.find((m) => m.role === "user")?.content.slice(0, 40) || "新对话";
    saveConversation(id, title, msgs, webOnRef.current, true)
      .then(onListUpdate)
      .catch(() => void 0);
  }

  function deleteMsg(i: number) {
    if (streaming) return;
    setMessages((prev) => {
      const c = [...prev];
      // 连带删掉紧随其后的那条 AI 回复
      const drop = c[i + 1]?.role === "assistant" ? 2 : 1;
      c.splice(i, drop);
      persistNow(c);
      return c;
    });
  }

  function editMsg(i: number) {
    if (streaming) return;
    setMessages((prev) => {
      const c = prev.slice(0, i); // 截断该消息及其后
      setInput(prev[i].content); // 内容回填输入框，改完重发
      persistNow(c);
      return c;
    });
  }

  async function loadConv(id: string) {
    try {
      const c = await getConversation(id);
      convIdRef.current = c.id;
      setWebOn(!!c.web); // 该会话各自的联网开关
      // 文件模式现在总是开启,不再从会话恢复
      jumpBottomRef.current = true;
      setMessages(c.messages);
    } catch {
      /* ignore */
    }
  }

  function newChat() {
    if (streaming) return;
    convIdRef.current = null;
    setMessages([]);
    setWebOn(false); // 新会话默认关联网
    // 文件模式始终开启
    setCfAwait(null);
  }

  function _persistFlags(web: boolean, file: boolean) {
    const id = convIdRef.current;
    if (!id || !messagesRef.current.length) return;
    const title =
      messagesRef.current.find((m) => m.role === "user")?.content.slice(
        0,
        40
      ) || "新对话";
    saveConversation(id, title, messagesRef.current, web, file).catch(
      () => void 0
    );
  }

  function toggleWeb() {
    const next = !webOnRef.current;
    setWebOn(next);
    _persistFlags(next, true); // 文件总开,只持久化 web 变化
  }

  async function persist(msgs: ChatMsg[]) {
    if (!msgs.length) return;
    let id = convIdRef.current;
    if (!id) {
      id = (await createConversation()).id;
      convIdRef.current = id;
      onConvChange(id);
    }
    const title =
      msgs.find((m) => m.role === "user")?.content.slice(0, 40) || "新对话";
    await saveConversation(id, title, msgs, webOnRef.current, true);
    onListUpdate();
  }

  function cfOnEvent(e: AgentEvent) {
    if (e.type === "run") {
      cfRunRef.current = e.run_id || "";
      return;
    }
    if (e.type === "confirm") {
      setCfAwait(e);
      setEditArgs(JSON.stringify(e.args ?? {}, null, 2));
      setStreaming(false);
      return;
    }
    if (e.type === "tool" || e.type === "result") {
      setMessages((prev) => {
        const c = [...prev];
        const last = c[c.length - 1];
        c[c.length - 1] = {
          ...last,
          events: [...(last.events || []), e],
        };
        return c;
      });
      return;
    }
    if (e.type === "answer") {
      setMessages((prev) => {
        const c = [...prev];
        c[c.length - 1] = { ...c[c.length - 1], content: e.content || "" };
        return c;
      });
      return;
    }
    if (e.type === "error") {
      setMessages((prev) => {
        const c = [...prev];
        const last = c[c.length - 1];
        c[c.length - 1] = {
          ...last,
          content: last.content + `\n\n> ⚠️ ${e.error}`,
        };
        return c;
      });
    }
  }

  function cfStart(history: ChatMsg[], mode: "file" | "settings") {
    const plain = history.map((m) => ({
      role: m.role,
      content: m.content,
    }));
    acRef.current = streamSSE(
      "/api/chatfs/start",
      { messages: plain, base: baseDirRef.current, mode },
      cfOnEvent,
      () => {
        setStreaming(false);
        if (!cfAwaitRef.current) persist(messagesRef.current);
      }
    );
  }

  function cfRespond(approve: boolean) {
    let ea: unknown;
    if (approve && editArgs.trim()) {
      try {
        ea = JSON.parse(editArgs);
      } catch {
        alert("参数不是合法 JSON");
        return;
      }
    }
    setCfAwait(null);
    setStreaming(true);
    acRef.current = streamSSE(
      "/api/chatfs/respond",
      { run_id: cfRunRef.current, approve, edited_args: ea },
      cfOnEvent,
      () => {
        setStreaming(false);
        if (!cfAwaitRef.current) persist(messagesRef.current);
      }
    );
  }

  function send() {
    const text = input.trim();
    if (!text || streaming || cfAwait) return;
    const history: ChatMsg[] = [...messages, { role: "user", content: text }];
    setMessages([...history, { role: "assistant", content: "", events: [] }]);
    setInput("");
    setStreaming(true);
    // 智能路由:明显涉及文件/目录操作 → 走工具循环(可读写文件,无 token 流);
    // 否则 → 走流式 streamChat(保留 token-by-token,无文件能力)。
    if (looksLikeFileTask(text)) {
      cfStart(history, "file");
      return;
    }
    acRef.current = streamChat(
      history,
      {
        onRoute: (r) =>
          setMessages((prev) => {
            const c = [...prev];
            const last = c[c.length - 1];
            c[c.length - 1] = { ...last, route: r };
            return c;
          }),
        onWeb: (n) =>
          setMessages((prev) => {
            const c = [...prev];
            const last = c[c.length - 1];
            c[c.length - 1] = { ...last, web: n };
            return c;
          }),
        onReasoning: (d) =>
          setMessages((prev) => {
            const c = [...prev];
            const last = c[c.length - 1];
            c[c.length - 1] = {
              ...last,
              reasoning: (last.reasoning || "") + d,
            };
            return c;
          }),
        onDelta: (d) =>
          setMessages((prev) => {
            const c = [...prev];
            const last = c[c.length - 1];
            c[c.length - 1] = { ...last, content: last.content + d };
            return c;
          }),
        onDone: () => {
          setStreaming(false);
          persist(messagesRef.current);
        },
        onError: (msg) => {
          setMessages((prev) => {
            const c = [...prev];
            const last = c[c.length - 1];
            c[c.length - 1] = {
              ...last,
              content: last.content + `\n\n> ⚠️ ${msg}`,
            };
            return c;
          });
          setStreaming(false);
          persist(messagesRef.current);
        },
      },
      webOn,
    );
  }

  function stop() {
    // 先发停止给后端 —— 这是关键:文件模式下后端的 subprocess.run 是阻塞
    // 的,光 abort SSE 流后端进程还在跑(等120s超时),会导致 RUNS 卡死
    // 下次发消息无法打开文件。chatfsStop 会强杀子进程 + 标 cancelled。
    const rid = cfRunRef.current;
    if (rid) {
      chatfsStop(rid).catch(() => {/* ignore */});
      cfRunRef.current = "";
    }
    acRef.current?.abort();
    setStreaming(false);
    setCfAwait(null);
    persist(messagesRef.current);
  }

  return (
    <div className="page chat">
      <div className="chat-head">
        <h1>对话</h1>
        <div className="chat-tools">
          <button
            className={"cfg-toggle" + (webOn ? " on" : "")}
            onClick={toggleWeb}
            title="开启后云端模型自决何时联网搜索(状态随该会话保留)"
          >
            🌐 联网{webOn ? "·开" : "·关"}
          </button>
          <ModelSwitcher
            ps={ps}
            compact
            onChange={async () => {
              const fresh = await getProviders();
              setPs(fresh);
            }}
          />
          {activeProv && !activeProv.api_key_set && (
            <span className="cfg-msg">（当前 API 无 key）</span>
          )}
        </div>
      </div>

      <div className="msgs" ref={msgsRef}>
        {initLoading && (
          <div className="loading-row">
            <span className="spinner" />
            <span>加载对话中…</span>
          </div>
        )}
        {messages.length === 0 && (
          <div className="empty">
            发条消息试试。Markdown / 代码高亮 / 图片 / Mermaid 图表 /
            视频音频都能渲染。
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={"msg " + m.role}>
            <div className="role">{m.role === "user" ? "你" : "AI"}</div>
            <div className="bubble">
              {m.role === "assistant" ? (
                <>
                  {m.events?.map((e, k) => {
                    if (e.type === "tool") {
                      // 没有后续 result = 这个工具正在跑(因为 tool/result
                      // 总是 1:1 顺序成对)。给个 spinner + 进行中提示,避免
                      // 用户分不清是卡住还是耗时长(run_command 跑脚本最常见)。
                      const inflight = !(m.events || [])
                        .slice(k + 1)
                        .some((x) => x.type === "result");
                      return (
                        <div
                          key={k}
                          className={
                            "ev ev-tool" + (inflight ? " inflight" : "")
                          }
                        >
                          {inflight ? (
                            <span
                              className="spinner"
                              style={{
                                width: 11,
                                height: 11,
                                borderWidth: 2,
                                marginRight: 6,
                                verticalAlign: "middle",
                              }}
                            />
                          ) : (
                            "▶ "
                          )}
                          <strong>{e.name}</strong>
                          {inflight && (
                            <span
                              className="muted"
                              style={{ marginLeft: 6, fontSize: 12 }}
                            >
                              {e.name === "run_command"
                                ? "运行中,可能需要一会儿…"
                                : "进行中…"}
                            </span>
                          )}
                          <pre>{JSON.stringify(e.args)}</pre>
                        </div>
                      );
                    }
                    return (
                      <div
                        key={k}
                        className={
                          "ev ev-res" +
                          ((e.result as { error?: string })?.error
                            ? " err"
                            : "")
                        }
                      >
                        {(e.result as { error?: string })?.error
                          ? "✖ "
                          : "✓ "}
                        {e.name}
                        <pre>
                          {JSON.stringify(e.result).slice(0, 2000)}
                        </pre>
                      </div>
                    );
                  })}
                  {/* 工具执行间隙(模型还没决定下一步) + 正在等模型 = 也显示
                       一个心跳;条件:streaming=true,但当前消息没在跑工具,
                       也没在出 token */}
                  {streaming &&
                    i === messages.length - 1 &&
                    !m.content &&
                    !m.reasoning &&
                    !(
                      m.events &&
                      m.events.length > 0 &&
                      m.events[m.events.length - 1].type === "tool"
                    ) && (
                      <div
                        className="muted"
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 6,
                          fontSize: 12,
                          marginBottom: 4,
                        }}
                      >
                        <span
                          className="spinner"
                          style={{
                            width: 11,
                            height: 11,
                            borderWidth: 2,
                          }}
                        />
                        {m.web !== undefined ? "AI 思考中…" : "AI 正在响应…"}
                      </div>
                    )}
                  {m.route && (
                    <div
                      className="route-badge"
                      title={m.route.reason || ""}
                    >
                      {m.route.mode === "local"
                        ? "🧠 "
                        : m.route.mode === "cloud"
                        ? "🧭 "
                        : "▸ "}
                      {m.route.name}
                      {m.route.mode === "cloud" && " (自动路由)"}
                    </div>
                  )}
                  {typeof m.web === "number" && (
                    <div className="route-badge">
                      🌐 已联网搜索 {m.web} 条
                    </div>
                  )}
                  {m.reasoning && (
                    <details
                      className="think"
                      open={
                        streaming &&
                        i === messages.length - 1 &&
                        !m.content
                      }
                    >
                      <summary>💭 思考过程</summary>
                      <div className="think-body">{m.reasoning}</div>
                    </details>
                  )}
                  <Markdown
                    text={m.content || (m.reasoning ? "" : "…")}
                    live={streaming && i === messages.length - 1}
                  />
                </>
              ) : (
                <>
                  <div className="user-text">{m.content}</div>
                  {!streaming && (
                    <div className="msg-actions">
                      <button onClick={() => editMsg(i)}>编辑</button>
                      <button onClick={() => deleteMsg(i)}>删除</button>
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        ))}
      </div>

      {cfAwait && (
        <div className="confirm-box">
          <div className="confirm-title">
            ⚠️ 文件高危操作待确认：<b>{cfAwait.tool}</b>（可改参数 JSON）
          </div>
          <textarea
            className="sys-area"
            value={editArgs}
            onChange={(e) => setEditArgs(e.target.value)}
          />
          <div className="cfg-actions">
            <button onClick={() => cfRespond(true)}>批准并执行</button>
            <button className="danger" onClick={() => cfRespond(false)}>
              拒绝
            </button>
          </div>
        </div>
      )}

      <div className="composer">
        <textarea
          value={input}
          placeholder="输入消息，Enter 发送，Shift+Enter 换行"
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
        />
        {streaming ? (
          <button className="stop" onClick={stop}>
            停止
          </button>
        ) : (
          <button onClick={send} disabled={!!cfAwait}>
            发送
          </button>
        )}
      </div>
    </div>
  );
}
