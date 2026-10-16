import { Check, Download, Loader2, Pencil, RotateCcw, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { api } from "../../services/api";
import { useApp } from "../../stores/app";
import type { LearningOverview, LearningSuggestion, ResponseCorrection, TrainingPreview } from "../../types/protocol";
import { formatRelativeDay } from "../../utils/format";

type Section = "overview" | "suggestions" | "corrections" | "speech" | "data";
const SECTIONS: [Section, string][] = [
  ["overview", "Genel bakış"],
  ["suggestions", "Öneriler"],
  ["corrections", "Düzeltmeler"],
  ["speech", "Ses tanıma"],
  ["data", "Veri"],
];
const STATUS_LABEL: Record<string, string> = {
  pending: "onay bekliyor",
  approved: "onaylı",
  rejected: "reddedildi",
  revoked: "geri alındı",
  superseded: "yerini yenisi aldı",
};
const KIND_LABEL: Record<ResponseCorrection["kind"], string> = { style: "üslup örneği", local: "yalnızca bu cevap", factual: "bilgi düzeltmesi" };
const BLOCK_LABEL: Record<string, string> = { secret: "gizli bilgi", identity: "kimlik değişikliği", instruction: "talimat içeriyor" };
const inputCls =
  "selectable w-full rounded-md border border-white/[0.08] bg-abyss px-2.5 py-1.5 text-[12.5px] text-ink outline-none focus:border-[color-mix(in_srgb,var(--accent)_45%,transparent)]";

function Small({
  onClick,
  children,
  danger,
  disabled,
  title,
}: {
  onClick: () => void;
  children: React.ReactNode;
  danger?: boolean;
  disabled?: boolean;
  title?: string;
}) {
  return (
    <button
      type="button"
      title={title}
      disabled={disabled}
      onClick={onClick}
      className={`flex items-center gap-1 rounded-md border px-2 py-0.5 text-[11px] transition-colors disabled:opacity-40 ${
        danger ? "border-white/[0.08] text-ink-dim hover:border-bad/50 hover:text-bad" : "border-white/[0.08] text-ink-dim hover:text-white"
      }`}
    >
      {children}
    </button>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-md border border-white/[0.06] bg-white/[0.015] px-3 py-2.5">
      <div className="font-mono text-[20px] text-white">{value}</div>
      <div className="hud-label !text-[9px]">{label}</div>
    </div>
  );
}

function useAction(onChanged: () => void) {
  const pushToast = useApp((st) => st.pushToast);
  return async (fn: () => Promise<unknown>) => {
    try {
      await fn();
      onChanged();
    } catch (e) {
      pushToast("error", (e as Error).message);
    }
  };
}

function SuggestionRow({ s, onChanged }: { s: LearningSuggestion; onChanged: () => void }) {
  const act = useAction(onChanged);
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(s.proposal);
  const conflict = s.note && !s.note.startsWith("{") ? s.note : null;
  return (
    <div className="rounded-md border border-white/[0.06] bg-white/[0.015] px-3 py-2.5">
      {editing ? (
        <div className="flex gap-1.5">
          <input value={text} onChange={(e) => setText(e.target.value)} className={inputCls} />
          <Small onClick={() => void act(() => api.editSuggestion(s.id, text)).then(() => setEditing(false))}>
            <Check size={11} /> Kaydet
          </Small>
        </div>
      ) : (
        <p className="selectable text-[13px] text-ink">{s.proposal}</p>
      )}
      <div className="mt-1.5 flex flex-wrap items-center gap-2 font-mono text-[9.5px] text-ink-faint">
        <span className="rounded border border-white/[0.08] px-1.5 py-[1px] text-ink-dim">{s.kind === "stt_term" ? "ses tanıma ipucu" : "konuşma tercihi"}</span>
        <span className={s.status === "pending" ? "text-warn" : ""}>{STATUS_LABEL[s.status]}</span>
        <span>· {s.evidence.length} kanıt</span>
        <span>· {formatRelativeDay(s.updated_at)}</span>
      </div>
      {conflict && <p className="mt-1.5 text-[11px] text-warn">{conflict}</p>}
      <div className="mt-2 flex flex-wrap gap-1.5">
        {(s.status === "pending" || s.status === "rejected") && (
          <Small onClick={() => void act(() => api.decideSuggestion(s.id, "approve", editing ? text : undefined))}>
            <Check size={11} /> Onayla
          </Small>
        )}
        {s.status === "pending" && (
          <Small onClick={() => void act(() => api.decideSuggestion(s.id, "reject"))}>
            <X size={11} /> Reddet
          </Small>
        )}
        {s.status === "approved" && (
          <Small onClick={() => void act(() => api.decideSuggestion(s.id, "revoke"))}>
            <RotateCcw size={11} /> Geri al
          </Small>
        )}
        {(s.status === "pending" || s.status === "approved") && !editing && (
          <Small onClick={() => setEditing(true)}>
            <Pencil size={11} /> Düzenle
          </Small>
        )}
      </div>
    </div>
  );
}

function CorrectionRow({ c, onChanged }: { c: ResponseCorrection; onChanged: () => void }) {
  const act = useAction(onChanged);
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(c.preferred_response);
  return (
    <div className="rounded-md border border-white/[0.06] bg-white/[0.015] px-3 py-2.5">
      {c.user_prompt && <div className="mb-1 text-[11.5px] text-ink-dim">Soru: {c.user_prompt}</div>}
      <div className="mb-1 text-[12px] text-ink-faint line-through decoration-white/20">{c.original_response.slice(0, 280)}</div>
      {editing ? (
        <div className="flex gap-1.5">
          <textarea value={text} rows={2} onChange={(e) => setText(e.target.value)} className={`${inputCls} resize-none`} />
          <Small onClick={() => void act(() => api.updateCorrection(c.id, { preferred_response: text })).then(() => setEditing(false))}>
            <Check size={11} /> Kaydet
          </Small>
        </div>
      ) : (
        <div className="selectable text-[13px] text-white">{c.preferred_response}</div>
      )}
      <div className="mt-1.5 flex flex-wrap items-center gap-2 font-mono text-[9.5px] text-ink-faint">
        <span className="rounded border border-white/[0.08] px-1.5 py-[1px] text-ink-dim">{KIND_LABEL[c.kind]}</span>
        <span className={c.usable_as_example ? "accent-text" : ""}>{c.usable_as_example ? "örnek olarak kullanılıyor" : STATUS_LABEL[c.status]}</span>
        {c.blocked_reason && <span className="text-bad">· engellendi: {BLOCK_LABEL[c.blocked_reason] ?? c.blocked_reason}</span>}
        {!c.message_id && <span>· konuşması silinmiş</span>}
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {c.status !== "approved" && !c.blocked_reason && c.kind !== "factual" && (
          <Small onClick={() => void act(() => api.updateCorrection(c.id, { status: "approved" }))}>
            <Check size={11} /> Onayla
          </Small>
        )}
        {c.status === "approved" && c.kind === "style" && (
          <Small onClick={() => void act(() => api.updateCorrection(c.id, { status: "rejected" }))}>
            <X size={11} /> Örnek olmaktan çıkar
          </Small>
        )}
        {c.kind === "factual" && !c.blocked_reason && (
          <Small title="Yanlışlıkla bilgi düzeltmesi sayıldıysa" onClick={() => void act(() => api.updateCorrection(c.id, { kind: "style", status: "approved" }))}>
            Üslup örneği yap
          </Small>
        )}
        {!editing && (
          <Small onClick={() => setEditing(true)}>
            <Pencil size={11} /> Düzenle
          </Small>
        )}
        <Small danger onClick={() => void act(() => api.deleteCorrection(c.id))}>
          <Trash2 size={11} /> Sil
        </Small>
      </div>
    </div>
  );
}

function DataSection({ data, onChanged }: { data: LearningOverview; onChanged: () => void }) {
  const settings = useApp((s) => s.settings);
  const update = useApp((s) => s.updateSettings);
  const pushToast = useApp((s) => s.pushToast);
  const [preview, setPreview] = useState<TrainingPreview | null>(null);
  const [skip, setSkip] = useState<Set<string>>(new Set());
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const exportFile = async (kind: "training" | "all") => {
    setBusy(true);
    try {
      const r = await api.exportFeedback(kind, [...skip]);
      pushToast("info", `${r.records} kayıt yerel dosyaya yazıldı: ${r.path}`);
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const enabled = settings?.feedback_examples_enabled ?? true;
  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-start justify-between gap-4 border-b border-white/[0.05] pb-4">
        <div>
          <div className="text-[12.5px] text-ink">Onaylı örnekleri cevaplarda kullan</div>
          <div className="mt-0.5 max-w-md text-[11px] leading-snug text-ink-faint">
            Benzer bir soru geldiğinde en fazla 2 onaylı düzeltmeni üslup örneği olarak gösterir. Kapatmak verileri silmez.
          </div>
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={enabled}
          onClick={() => settings && void update({ feedback_examples_enabled: !enabled })}
          className="relative h-5 w-9 shrink-0 rounded-full transition-colors"
          style={{ background: enabled ? "var(--accent)" : "rgb(255 255 255 / 0.12)" }}
        >
          <span className="absolute top-0.5 h-4 w-4 rounded-full bg-white transition-all" style={{ left: enabled ? 18 : 2 }} />
        </button>
      </div>

      <div>
        <div className="mb-1 text-[12.5px] text-ink">Eğitim verisi dışa aktarımı (JSONL)</div>
        <p className="mb-2 text-[11px] leading-snug text-ink-faint">
          Yalnızca onayladığın üslup düzeltmeleri. Gizli bilgi, kimlik ve talimat içerenler otomatik çıkarılır. Dosya yalnızca bu Mac'e
          (~/Downloads/LAZOS) yazılır, hiçbir yere gönderilmez. Bu sürüm modeli eğitmez.
        </p>
        {!preview ? (
          <Small onClick={() => void api.exportPreview().then(setPreview).catch((e) => pushToast("error", (e as Error).message))}>Önizle</Small>
        ) : (
          <div className="rounded-md border border-white/[0.06] bg-white/[0.015] p-2.5">
            {preview.records.length === 0 && <p className="text-[11.5px] text-ink-faint">Dışa aktarılacak onaylı örnek yok.</p>}
            {preview.records.map((r) => (
              <label key={r.id} className="flex items-start gap-2 border-b border-white/[0.04] py-1.5 text-[11.5px] last:border-0">
                <input
                  type="checkbox"
                  checked={!skip.has(r.id)}
                  onChange={(e) => {
                    const next = new Set(skip);
                    if (e.target.checked) next.delete(r.id);
                    else next.add(r.id);
                    setSkip(next);
                  }}
                  className="mt-0.5 accent-[var(--accent)]"
                />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-ink-dim">{r.instruction}</span>
                  <span className="block truncate text-ink">{r.preferred_response}</span>
                  {r.warnings.length > 0 && <span className="text-warn">Dikkat: {r.warnings.join(", ")}</span>}
                </span>
              </label>
            ))}
            {preview.excluded.length > 0 && (
              <p className="mt-1.5 text-[10.5px] text-ink-faint">{preview.excluded.length} kayıt güvenlik nedeniyle hariç tutuldu.</p>
            )}
            <div className="mt-2 flex gap-1.5">
              <Small disabled={busy || preview.records.length === skip.size} onClick={() => void exportFile("training")}>
                <Download size={11} /> Seçilenleri kaydet
              </Small>
            </div>
          </div>
        )}
      </div>

      <div className="flex flex-wrap gap-1.5">
        <Small disabled={busy} onClick={() => void exportFile("all")}>
          <Download size={11} /> Tüm geri bildirimi dışa aktar (JSON)
        </Small>
        <Small
          danger
          onClick={() => {
            if (!armed) return setArmed(true);
            setArmed(false);
            void api.clearFeedback().then(() => {
              pushToast("info", "Geri bildirim geçmişi temizlendi. Onaylı tercihler Hafıza'da kalır.");
              onChanged();
            });
          }}
        >
          <Trash2 size={11} /> {armed ? "Kalıcı olarak temizle — onayla" : "Geri bildirim geçmişini temizle"}
        </Small>
      </div>
      <p className="text-[10.5px] text-ink-faint">
        Toplam {data.stats.feedback_total} değerlendirme, {data.stats.corrections} düzeltme.
      </p>
    </div>
  );
}

export function LearningTab() {
  const revision = useApp((s) => s.learningRevision);
  const pushToast = useApp((s) => s.pushToast);
  const [data, setData] = useState<LearningOverview | null>(null);
  const [section, setSection] = useState<Section>("overview");
  const load = useCallback(() => {
    api.learning().then(setData).catch((e) => pushToast("error", (e as Error).message));
  }, [pushToast]);
  useEffect(load, [load, revision]);
  if (!data) return <Loader2 size={16} className="mx-auto mt-10 animate-spin accent-text" />;
  const pending = data.suggestions.filter((s) => s.status === "pending").length;

  return (
    <div>
      <p className="mb-3 text-[12px] leading-relaxed text-ink-dim">
        LAZOS modeli yeniden eğitmez. Onayladığın düzeltmeleri benzer sorularda üslup örneği olarak görür; tekrar eden geri bildirimden
        yalnızca öneri üretir ve tercih ancak sen onaylarsan Hafıza'ya yazılır. Her şey geri alınabilir.
      </p>
      <div className="mb-4 flex flex-wrap gap-1.5">
        {SECTIONS.map(([id, label]) => (
          <button
            key={id}
            type="button"
            onClick={() => setSection(id)}
            className={`rounded-full border px-2.5 py-0.5 text-[11px] transition-colors ${
              section === id ? "accent-border bg-white/[0.08] text-white" : "border-white/[0.07] text-ink-dim hover:text-white"
            }`}
          >
            {label}
            {id === "suggestions" && pending > 0 && <span className="ml-1 font-mono text-warn">{pending}</span>}
          </button>
        ))}
      </div>

      {section === "overview" && (
        <div>
          <div className="grid grid-cols-3 gap-2">
            <Stat label="Değerlendirme" value={data.stats.feedback_total} />
            <Stat label="Olumlu" value={data.stats.positive} />
            <Stat label="Olumsuz" value={data.stats.negative} />
            <Stat label="Onaylı örnek" value={data.stats.approved_examples} />
            <Stat label="Bekleyen öneri" value={data.stats.pending_suggestions} />
            <Stat label="Ses düzeltmesi" value={data.stats.transcript_corrections} />
          </div>
          {Object.keys(data.stats.reasons).length > 0 && (
            <div className="mt-4">
              <div className="hud-label mb-2 !text-[9px] accent-text">En sık nedenler</div>
              {Object.entries(data.stats.reasons)
                .sort((a, b) => (b[1] ?? 0) - (a[1] ?? 0))
                .map(([reason, n]) => (
                  <div key={reason} className="flex justify-between py-0.5 text-[12px] text-ink-dim">
                    <span>{data.reasons[reason as keyof typeof data.reasons] ?? reason}</span>
                    <span className="font-mono">{n}</span>
                  </div>
                ))}
            </div>
          )}
        </div>
      )}

      {section === "suggestions" && (
        <div className="flex flex-col gap-1.5">
          {data.suggestions.length === 0 && (
            <p className="py-6 text-center text-[12px] text-ink-faint">
              Henüz öneri yok. Aynı türden geri bildirim birkaç kez tekrarlanınca burada öneri oluşur.
            </p>
          )}
          {data.suggestions.map((s) => (
            <SuggestionRow key={`${s.id}-${s.updated_at}`} s={s} onChanged={load} />
          ))}
        </div>
      )}

      {section === "corrections" && (
        <div className="flex flex-col gap-1.5">
          {data.corrections.length === 0 && (
            <p className="py-6 text-center text-[12px] text-ink-faint">Henüz düzeltme yok. Bir cevabın altındaki kalem simgesiyle ekleyebilirsin.</p>
          )}
          {data.corrections.map((c) => (
            <CorrectionRow key={`${c.id}-${c.updated_at}`} c={c} onChanged={load} />
          ))}
        </div>
      )}

      {section === "speech" && (
        <div className="flex flex-col gap-1.5">
          {data.transcripts.length === 0 && (
            <p className="py-6 text-center text-[12px] text-ink-faint">Sesli mesajındaki kalem simgesiyle yanlış tanınan metni düzeltebilirsin.</p>
          )}
          {data.transcripts.map((t) => (
            <div key={t.id} className="flex items-start gap-3 rounded-md border border-white/[0.06] bg-white/[0.015] px-3 py-2">
              <div className="min-w-0 flex-1 text-[12px]">
                <div className="text-ink-faint line-through decoration-white/20">{t.original_text}</div>
                <div className="text-ink">{t.corrected_text}</div>
                {t.terms.length > 0 && <div className="mt-0.5 font-mono text-[9.5px] text-ink-faint">yeni terimler: {t.terms.join(", ")}</div>}
              </div>
              <Small danger onClick={() => void api.deleteTranscriptCorrection(t.id).then(load)}>
                <Trash2 size={11} />
              </Small>
            </div>
          ))}
        </div>
      )}

      {section === "data" && <DataSection data={data} onChanged={load} />}
    </div>
  );
}
