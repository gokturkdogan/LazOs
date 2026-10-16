import { Bug, ExternalLink, Globe, Loader2, SearchX } from "lucide-react";

import { openExternal } from "../../services/backend";
import { useApp } from "../../stores/app";
import type { ResearchProgress, Source } from "../../types/protocol";

export function ResearchStatus({ research, streaming }: { research: ResearchProgress; streaming: boolean }) {
  if (research.status === "searching") {
    return (
      <div className="relative mb-3 overflow-hidden rounded-md border border-white/[0.06] bg-white/[0.02] px-3 py-2">
        <div className="absolute inset-y-0 left-0 w-1/3 bg-gradient-to-r from-transparent via-[color-mix(in_srgb,var(--accent)_18%,transparent)] to-transparent animate-scan" />
        <div className="relative flex items-center gap-2">
          <Loader2 size={12} className="animate-spin accent-text" />
          <span className="hud-label !text-[9px] accent-text">Web araştırması</span>
          <span className="truncate font-mono text-[11px] text-ink-dim">“{research.query}”</span>
        </div>
      </div>
    );
  }
  if (research.status === "failed") {
    return (
      <div className="mb-3 flex items-center gap-2 rounded-md border border-[color-mix(in_srgb,var(--color-warn)_30%,transparent)] bg-[color-mix(in_srgb,var(--color-warn)_6%,transparent)] px-3 py-2 text-[11.5px] text-ink-dim">
        <SearchX size={13} className="text-warn" />
        Web araştırması başarısız oldu{research.error ? ` (${research.error})` : ""}. Yanıt genel bilgiye dayanıyor.
      </div>
    );
  }
  if (streaming) {
    return (
      <div className="mb-3 flex items-center gap-2 text-[11px] text-ink-faint">
        <Globe size={12} className="accent-text" />
        <span className="font-mono">
          {research.sources?.length ?? 0} kaynak · {research.provider ?? "web"} · “{research.query}”
        </span>
      </div>
    );
  }
  return null;
}

export function SourceList({ sources, query, messageId }: { sources: Source[]; query?: string; messageId?: string }) {
  const diagnostics = useApp((s) => s.settings?.research_diagnostics ?? false);
  const setUi = useApp((s) => s.setUi);
  if (!sources.length) return null;
  return (
    <div className="mt-3.5">
      <div className="mb-2 flex items-center gap-2">
        <span className="hud-label !text-[9px] accent-text">Kaynaklar</span>
        <span className="h-px flex-1 bg-gradient-to-r from-white/10 to-transparent" />
        {query && <span className="truncate font-mono text-[9.5px] text-ink-faint">“{query}”</span>}
        {diagnostics && messageId && (
          <button
            type="button"
            onClick={() => setUi({ researchTraceId: messageId })}
            className="flex shrink-0 items-center gap-1 rounded border border-white/[0.08] px-1.5 py-0.5 font-mono text-[9.5px] text-ink-dim hover:text-white"
          >
            <Bug size={10} /> tanılama
          </button>
        )}
      </div>
      <div className="grid grid-cols-1 gap-1.5">
        {sources.map((s) => (
          <button
            key={s.id}
            type="button"
            onClick={() => void openExternal(s.url)}
            title={s.url}
            className={`group flex items-start gap-2.5 rounded-md border px-2.5 py-2 text-left transition-colors hover:bg-white/[0.04] ${
              s.cited === false ? "border-white/[0.04] opacity-60" : "border-white/[0.07] bg-white/[0.015]"
            }`}
          >
            <span className="mt-[1px] grid h-[18px] min-w-[18px] place-items-center rounded-[4px] border font-mono text-[9.5px] accent-border" style={{ color: "var(--accent-2)" }}>
              {s.id}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[12px] text-ink group-hover:text-white">{s.title}</span>
              <span className="mt-0.5 flex items-center gap-1.5 font-mono text-[10px] text-ink-faint">
                {s.domain}
                {s.published && <span>· {s.published.slice(0, 10)}</span>}
              </span>
            </span>
            <ExternalLink size={12} className="mt-0.5 shrink-0 text-ink-faint opacity-0 transition-opacity group-hover:opacity-100" />
          </button>
        ))}
      </div>
    </div>
  );
}
