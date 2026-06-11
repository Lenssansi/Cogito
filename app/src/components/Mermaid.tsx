import { useEffect, useState } from "react";
import mermaid from "mermaid";

mermaid.initialize({ startOnLoad: false, theme: "dark", securityLevel: "strict" });

let seq = 0;

// live=true：消息还在流式输出，源码可能只到一半，无效时只占位、绝不报错。
export default function Mermaid({
  code,
  live,
}: {
  code: string;
  live: boolean;
}) {
  const [svg, setSvg] = useState("");
  const [err, setErr] = useState("");
  const [showCode, setShowCode] = useState(false);

  useEffect(() => {
    let alive = true;
    const id = `mmd-${++seq}`;
    (async () => {
      const valid = await mermaid.parse(code, { suppressErrors: true });
      if (!alive) return;
      if (!valid) {
        setSvg("");
        setErr(live ? "" : "语法不合法（标签含 []、<、= 等需加引号）");
        return;
      }
      try {
        const r = await mermaid.render(id, code);
        if (!alive) return;
        setSvg(r.svg);
        setErr("");
      } catch (e) {
        if (alive) setErr(String((e as Error)?.message || e));
      } finally {
        document.getElementById(id)?.remove();
        document.getElementById("d" + id)?.remove();
      }
    })();
    return () => {
      alive = false;
    };
  }, [code, live]);

  const pending = !svg && !err;

  return (
    <div className="mermaid-wrap">
      <div className="codeblock-bar">
        <span>mermaid{err ? "（解析失败）" : ""}</span>
        <button onClick={() => setShowCode((s) => !s)}>
          {showCode ? "显示图表" : "显示代码"}
        </button>
      </div>
      {showCode ? (
        <pre className="mermaid-code">
          <code>{code}</code>
        </pre>
      ) : pending ? (
        <div className="mermaid-box pending">图表生成中…</div>
      ) : err ? (
        <>
          <div className="mermaid-err">图表解析失败：{err}</div>
          <pre className="mermaid-code">
            <code>{code}</code>
          </pre>
        </>
      ) : (
        <div
          className="mermaid-box"
          dangerouslySetInnerHTML={{ __html: svg }}
        />
      )}
    </div>
  );
}
