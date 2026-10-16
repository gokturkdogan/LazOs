import { Check, CircleSlash, Copy, Loader2, X } from "lucide-react";
import { motion } from "motion/react";
import { useEffect, useState } from "react";

import { api } from "../../services/api";
import { useApp } from "../../stores/app";
import type { ResearchEvidence, ResearchTrace, StandingRow } from "../../types/protocol";

const STATUS: Record<ResearchEvidence["status"], [string, string]> = {
  verified: ["doğrulandı", "var(--color-ok, #34d399)"],
  single_source: ["tek kaynak", "var(--accent)"],
  conflict: ["çelişki", "var(--color-warn, #f59e0b)"],
  insufficient: ["kanıt yok", "var(--color-err, #f87171)"],
};
const TIMING_LABEL: Record<string, string> = {
  search: "arama",
  fetch: "sayfa okuma",
  evidence: "kanıt",
  total: "araştırma toplam",
  ttft_from_turn_start: "ilk token (tur başından)",
};

function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="border-t border-white/[0.05] py-3">
      <h3 className="hud-label mb-2 !text-[9px] accent-text">{title}</h3>
      {children}
    </section>
  );
}

function describe(r: StandingRow): string {
  const wdl = r.won != null && r.drawn != null && r.lost != null ? ` (${r.won}G ${r.drawn}B ${r.lost}M)` : "";
  return `${r.rank}. sıra · ${r.team} · ${r.points ?? "?"} puan${r.played != null ? ` · ${r.played} maç${wdl}` : ""}`;
}

function TraceView({ t }: { t: ResearchTrace }) {
  const [showContext, setShowContext] = useState(false);
  const ev = t.evidence;
  return (
    <div className="text-[12px] text-ink">
      <Block title="İstek ve algılanan niyet">
        <p className="selectable">“{t.request}”</p>
        <p className="mt-1 font-mono text-[10.5px] text-ink-dim">
          {[t.intent.kind, t.intent.want, t.intent.entity, t.intent.competition, t.intent.season].filter(Boolean).join(" · ")}
        </p>
      </Block>

      <Block title="Arama sorguları">
        {t.queries.map((q, i) => (
          <p key={i} className="selectable font-mono text-[11px]">
            {i + 1}. {q} <span className="text-ink-faint">({t.provider ?? "—"})</span>
          </p>
        ))}
      </Block>

      <Block title="Süreler">
        <div className="flex flex-wrap gap-1.5">
          {Object.entries(t.timings_ms).map(([k, v]) => (
            <span key={k} className="rounded border border-white/[0.07] px-1.5 py-0.5 font-mono text-[10.5px] text-ink-dim">
              {TIMING_LABEL[k] ?? k}: {Math.round(v)} ms
            </span>
          ))}
        </div>
      </Block>

      <Block title={`Arama sonuçları (${t.candidates.filter((c) => c.selected).length} seçildi / ${t.candidates.length})`}>
        <div className="flex flex-col gap-1">
          {t.candidates.map((c, i) => (
            <div key={i} className={`rounded border px-2 py-1.5 ${c.selected ? "border-white/[0.1] bg-white/[0.02]" : "border-white/[0.04] opacity-70"}`}>
              <div className="flex items-center gap-2">
                {c.selected ? <Check size={11} className="shrink-0 accent-text" /> : <CircleSlash size={11} className="shrink-0 text-ink-faint" />}
                <span className="w-9 shrink-0 font-mono text-[10px] text-ink-faint">{c.score.toFixed(1)}</span>
                <span className="shrink-0 font-mono text-[10.5px] text-ink-dim">{c.domain}</span>
                <span className="truncate text-[11.5px]">{c.title}</span>
              </div>
              <p className="ml-[3.6rem] mt-0.5 text-[10.5px] text-ink-faint">
                {c.rejected ? `elendi: ${c.rejected}` : c.reasons.join(" · ") || "—"}
              </p>
            </div>
          ))}
        </div>
      </Block>

      <Block title="Okunan sayfalar">
        {t.fetches.length === 0 && <p className="text-ink-faint">Sayfa açılmadı.</p>}
        {t.fetches.map((f, i) => (
          <p key={i} className="selectable font-mono text-[10.5px] text-ink-dim">
            {f.status === "ok" ? "✓" : "✗"} {new URL(f.url).hostname} · {f.status}
            {f.fetch_ms != null && ` · ${Math.round(f.fetch_ms)} ms`}
            {f.bytes != null && ` · ${Math.round(f.bytes / 1024)} KB`}
            {f.tables != null && ` · ${f.tables} tablo (${f.json_tables ?? 0} JSON)`}
            {f.standings_rows != null && ` · puan durumu ${f.standings_rows} satır`}
            {f.rejected_tables?.length ? ` · elenen tablo: ${f.rejected_tables.join("; ")}` : ""}
          </p>
        ))}
      </Block>

      {ev && (
        <Block title="Çıkarılan kanıt">
          <div className="flex items-center gap-2">
            <span className="rounded px-1.5 py-0.5 font-mono text-[10px] text-void" style={{ background: STATUS[ev.status][1] }}>
              {STATUS[ev.status][0]}
            </span>
            <span className="text-[11.5px]">
              {ev.competition} {ev.season}
              {ev.row ? ` — ${describe(ev.row)}` : ""}
            </span>
          </div>
          {ev.agreeing.length > 0 && <p className="mt-1 font-mono text-[10.5px] text-ink-dim">Kaynak: {ev.agreeing.map((u) => new URL(u).hostname).join(", ")}</p>}
          {ev.notes.map((n, i) => (
            <p key={i} className="mt-1 text-[11px] text-warn">
              {n}
            </p>
          ))}
        </Block>
      )}

      <Block title="Modele verilen araştırma içeriği">
        <button type="button" onClick={() => setShowContext((v) => !v)} className="text-[11px] text-ink-dim hover:text-white">
          {showContext ? "Gizle" : `Göster (${t.context.length} karakter)`}
        </button>
        {showContext && (
          <pre className="selectable mt-2 max-h-72 overflow-auto whitespace-pre-wrap rounded bg-black/30 p-2 font-mono text-[10.5px] text-ink-dim">{t.context}</pre>
        )}
      </Block>

      <Block title="Son cevap">
        <p className="selectable whitespace-pre-wrap">{t.answer || "—"}</p>
      </Block>

      {(t.fallbacks.length > 0 || t.errors.length > 0) && (
        <Block title="Geri dönüşler ve hatalar">
          {t.fallbacks.map((f, i) => (
            <p key={`f${i}`} className="text-[11px] text-ink-dim">
              ↪ {f}
            </p>
          ))}
          {t.errors.map((e, i) => (
            <p key={`e${i}`} className="text-[11px] text-warn">
              ⚠ {e}
            </p>
          ))}
        </Block>
      )}
    </div>
  );
}

/** Developer-only view of how one answer's web research went (enabled in Ayarlar › Web araştırması). */
export function ResearchDebugPanel({ messageId }: { messageId: string }) {
  const setUi = useApp((s) => s.setUi);
  const [traces, setTraces] = useState<ResearchTrace[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    api
      .researchTraces(messageId)
      .then(setTraces)
      .catch((e) => setError((e as Error).message));
  }, [messageId]);

  const copy = () => {
    // Web data and the request only: no conversation history, memories or credentials are in a trace.
    void navigator.clipboard.writeText(JSON.stringify(traces, null, 2)).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="absolute inset-0 z-50 flex items-center justify-center bg-void/55 p-6 backdrop-blur-sm"
      onClick={() => setUi({ researchTraceId: null })}
    >
      <motion.div
        initial={{ y: 14, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        exit={{ y: 10, opacity: 0 }}
        transition={{ duration: 0.3, ease: [0.2, 0.8, 0.2, 1] }}
        onClick={(e) => e.stopPropagation()}
        className="glass-strong brackets relative flex h-[min(780px,100%)] w-[860px] max-w-full flex-col overflow-hidden rounded-xl"
      >
        <header className="flex h-12 shrink-0 items-center gap-3 border-b border-white/[0.06] px-5">
          <span className="hud-label flex-1 accent-text">Araştırma tanılaması</span>
          {traces && (
            <button type="button" onClick={copy} className="flex items-center gap-1.5 text-[11px] text-ink-dim hover:text-white">
              {copied ? <Check size={12} /> : <Copy size={12} />} JSON kopyala
            </button>
          )}
          <button type="button" onClick={() => setUi({ researchTraceId: null })} className="grid h-7 w-7 place-items-center rounded-md text-ink-dim hover:bg-white/[0.06] hover:text-white" aria-label="Kapat">
            <X size={15} />
          </button>
        </header>
        <div className="flex-1 overflow-y-auto px-5 pb-5">
          {error && <p className="py-6 text-center text-[12px] text-ink-dim">Bu yanıt için tanılama yok. Tanılama, ayar açıldıktan sonraki araştırmalar için tutulur. ({error})</p>}
          {!traces && !error && (
            <div className="grid place-items-center py-10">
              <Loader2 size={16} className="animate-spin accent-text" />
            </div>
          )}
          {traces?.map((t, i) => (
            <div key={i}>
              {traces.length > 1 && <h2 className="mt-4 text-[11px] text-ink-faint">Araştırma {i + 1} / {traces.length}</h2>}
              <TraceView t={t} />
            </div>
          ))}
        </div>
      </motion.div>
    </motion.div>
  );
}
