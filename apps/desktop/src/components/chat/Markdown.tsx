import { memo, useMemo } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";

import { openExternal } from "../../services/backend";
import type { Source } from "../../types/protocol";

/** Turn bare citation markers like [1] into links (outside code) so they render as source chips. */
export function linkCitations(text: string): string {
  return text
    .split(/(```[\s\S]*?(?:```|$)|`[^`\n]*`)/g)
    .map((part, i) => (i % 2 === 1 ? part : part.replace(/\[(\d{1,2})\](?![(:])/g, "[$1](#cite-$1)")))
    .join("");
}

export const Markdown = memo(function Markdown({ text, sources }: { text: string; sources: Source[] }) {
  const content = useMemo(() => linkCitations(text), [text]);
  const components = useMemo<Components>(
    () => ({
      a({ href, children }) {
        if (href?.startsWith("#cite-")) {
          const id = Number(href.slice(6));
          const src = sources.find((s) => s.id === id);
          return (
            <button
              type="button"
              onClick={() => src && void openExternal(src.url)}
              title={src ? `${src.title} — ${src.domain}` : "Kaynak"}
              disabled={!src}
              className="mx-[1px] inline-flex h-[17px] min-w-[17px] -translate-y-[1px] items-center justify-center rounded-[4px] border px-1 align-middle font-mono text-[9.5px] leading-none transition-colors accent-border hover:bg-white/10 disabled:opacity-40"
              style={{ color: "var(--accent-2)" }}
            >
              {id}
            </button>
          );
        }
        return (
          <a
            href={href}
            onClick={(e) => {
              e.preventDefault();
              if (href) void openExternal(href);
            }}
          >
            {children}
          </a>
        );
      },
    }),
    [sources],
  );
  return (
    <div className="prose-lazos selectable">
      <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[[rehypeHighlight, { detect: true, ignoreMissing: true }]]} components={components}>
        {content}
      </ReactMarkdown>
    </div>
  );
});
