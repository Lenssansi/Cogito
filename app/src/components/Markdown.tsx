import { useMemo, useState } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import hljs from "highlight.js/lib/common";
import "highlight.js/styles/github-dark.css";
import Mermaid from "./Mermaid";

function highlight(code: string, lang?: string): string {
  try {
    if (lang && hljs.getLanguage(lang))
      return hljs.highlight(code, { language: lang }).value;
    return hljs.highlightAuto(code).value;
  } catch {
    return code.replace(/[&<>]/g, (c) =>
      c === "&" ? "&amp;" : c === "<" ? "&lt;" : "&gt;"
    );
  }
}

function CodeBlock({ lang, raw }: { lang?: string; raw: string }) {
  const [copied, setCopied] = useState(false);
  const html = useMemo(() => highlight(raw, lang), [raw, lang]);
  return (
    <div className="codeblock">
      <div className="codeblock-bar">
        <span>{lang || "code"}</span>
        <button
          onClick={() => {
            navigator.clipboard.writeText(raw);
            setCopied(true);
            setTimeout(() => setCopied(false), 1200);
          }}
        >
          {copied ? "已复制" : "复制"}
        </button>
      </div>
      <pre>
        <code
          className={"hljs" + (lang ? " language-" + lang : "")}
          dangerouslySetInnerHTML={{ __html: html }}
        />
      </pre>
    </div>
  );
}

const isMedia = (h: string, exts: string[]) =>
  exts.some((e) => h.toLowerCase().split("?")[0].endsWith(e));

export default function Markdown({
  text,
  live = false,
}: {
  text: string;
  live?: boolean;
}) {
  const components = useMemo<Components>(
    () => ({
      code(props) {
        const { className, children } = props;
        // 不用 rehype-highlight：children 就是纯源码字符串，
        // mermaid / 复制 都拿得到原始文本（之前的 bug 根因）。
        const raw = String(children).replace(/\n$/, "");
        const lang = /language-(\w+)/.exec(className || "")?.[1];
        if (lang === "mermaid") return <Mermaid code={raw} live={live} />;
        if (!lang && !raw.includes("\n"))
          return <code className="inline-code">{children}</code>;
        return <CodeBlock lang={lang} raw={raw} />;
      },
      pre: ({ children }) => <>{children}</>,
      a({ href = "", children }) {
        if (isMedia(href, [".mp4", ".webm", ".ogg"]))
          return <video src={href} controls className="md-media" />;
        if (isMedia(href, [".mp3", ".wav", ".m4a"]))
          return <audio src={href} controls className="md-media" />;
        return (
          <a href={href} target="_blank" rel="noreferrer">
            {children}
          </a>
        );
      },
      img: ({ src = "", alt }) => (
        <img src={src} alt={alt} className="md-media" loading="lazy" />
      ),
    }),
    [live]
  );

  return (
    <div className="md">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {text}
      </ReactMarkdown>
    </div>
  );
}
