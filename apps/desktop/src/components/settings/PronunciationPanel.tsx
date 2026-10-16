import { Check, Loader2, Pencil, Plus, Trash2, Volume2, X } from "lucide-react";
import { motion } from "motion/react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { api } from "../../services/api";
import { useApp } from "../../stores/app";
import type { PronunciationOverview, PronunciationRule } from "../../types/protocol";

type Engine = PronunciationRule["engine"];
const ENGINE_LABEL: Record<Engine, string> = { all: "Tüm sesler", piper: "Piper", macos: "Yelda" };
const inputCls =
  "selectable rounded-md border border-white/[0.08] bg-abyss px-2.5 py-1.5 text-[12.5px] text-ink outline-none focus:border-[color-mix(in_srgb,var(--accent)_45%,transparent)]";

function RuleForm({
  initial,
  onSubmit,
  onCancel,
  submitLabel,
}: {
  initial?: Partial<PronunciationRule>;
  onSubmit: (rule: Pick<PronunciationRule, "term" | "replacement" | "engine" | "case_sensitive">) => Promise<void>;
  onCancel?: () => void;
  submitLabel: string;
}) {
  const [term, setTerm] = useState(initial?.term ?? "");
  const [replacement, setReplacement] = useState(initial?.replacement ?? "");
  const [engine, setEngine] = useState<Engine>(initial?.engine ?? "all");
  const [caseSensitive, setCaseSensitive] = useState(initial?.case_sensitive ?? false);
  const valid = term.trim() && replacement.trim();
  return (
    <form
      className="flex flex-wrap items-center gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (valid) void onSubmit({ term: term.trim(), replacement: replacement.trim(), engine, case_sensitive: caseSensitive });
      }}
    >
      <input value={term} onChange={(e) => setTerm(e.target.value)} placeholder="Yazım (ör. backend)" className={`${inputCls} w-36`} />
      <span className="text-ink-faint">→</span>
      <input value={replacement} onChange={(e) => setReplacement(e.target.value)} placeholder="Okunuş (ör. bek end)" className={`${inputCls} w-40`} />
      <select value={engine} onChange={(e) => setEngine(e.target.value as Engine)} className={`${inputCls} w-28`}>
        {(Object.keys(ENGINE_LABEL) as Engine[]).map((k) => (
          <option key={k} value={k}>
            {ENGINE_LABEL[k]}
          </option>
        ))}
      </select>
      <label className="flex items-center gap-1.5 text-[11px] text-ink-dim">
        <input type="checkbox" checked={caseSensitive} onChange={(e) => setCaseSensitive(e.target.checked)} className="accent-[var(--accent)]" />
        Büyük/küçük harf duyarlı
      </label>
      <button
        type="submit"
        disabled={!valid}
        className="flex items-center gap-1 rounded-md px-3 py-1.5 text-[12px] font-medium text-void disabled:opacity-30"
        style={{ background: "var(--accent)" }}
      >
        {submitLabel === "Ekle" ? <Plus size={12} /> : <Check size={12} />} {submitLabel}
      </button>
      {onCancel && (
        <button type="button" onClick={onCancel} className="px-1.5 text-[11.5px] text-ink-dim hover:text-white">
          Vazgeç
        </button>
      )}
    </form>
  );
}

function RuleRow({ rule, onChanged, onCustomize }: { rule: PronunciationRule; onChanged: () => void; onCustomize: (r: PronunciationRule) => void }) {
  const pushToast = useApp((s) => s.pushToast);
  const [editing, setEditing] = useState(false);
  const [confirm, setConfirm] = useState(false);
  if (editing) {
    return (
      <div className="rounded-md border accent-border bg-white/[0.03] px-3 py-2.5">
        <RuleForm
          initial={rule}
          submitLabel="Kaydet"
          onCancel={() => setEditing(false)}
          onSubmit={async (r) => {
            try {
              await api.updatePronunciation(rule.id, r);
              setEditing(false);
              onChanged();
            } catch (e) {
              pushToast("error", (e as Error).message);
            }
          }}
        />
      </div>
    );
  }
  return (
    <div className={`group flex items-center gap-3 rounded-md border px-3 py-2 ${rule.enabled ? "border-white/[0.06] bg-white/[0.015]" : "border-white/[0.04] opacity-50"}`}>
      <span className="w-36 truncate font-mono text-[12px] text-ink">{rule.term}</span>
      <span className="text-ink-faint">→</span>
      <span className="flex-1 truncate text-[12.5px] text-white">{rule.replacement}</span>
      <span className="rounded border border-white/[0.08] px-1.5 py-[1px] font-mono text-[9.5px] text-ink-dim">{ENGINE_LABEL[rule.engine]}</span>
      {rule.builtin && !rule.verified && (
        <span className="font-mono text-[9.5px] text-warn" title="Otomatik dinleme testinde doğrulanamadı; insan kulağıyla kontrol edilmeli.">
          doğrulanmadı
        </span>
      )}
      <div className="flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100">
        {rule.builtin ? (
          <button type="button" onClick={() => onCustomize(rule)} className="rounded px-1.5 py-0.5 text-[10.5px] text-ink-dim hover:bg-white/10 hover:text-white">
            Özelleştir
          </button>
        ) : (
          <>
            <button type="button" title={rule.enabled ? "Devre dışı bırak" : "Etkinleştir"} onClick={() => void api.updatePronunciation(rule.id, { enabled: !rule.enabled }).then(onChanged)} className="rounded px-1.5 py-0.5 text-[10.5px] text-ink-dim hover:bg-white/10 hover:text-white">
              {rule.enabled ? "Kapat" : "Aç"}
            </button>
            <button type="button" title="Düzenle" onClick={() => setEditing(true)} className="grid h-6 w-6 place-items-center rounded text-ink-faint hover:bg-white/10 hover:text-white">
              <Pencil size={11} />
            </button>
            {confirm ? (
              <button type="button" onClick={() => void api.deletePronunciation(rule.id).then(onChanged)} className="rounded px-1.5 py-0.5 font-mono text-[10px] text-bad hover:bg-bad/10">
                SİL
              </button>
            ) : (
              <button type="button" title="Sil" onClick={() => setConfirm(true)} className="grid h-6 w-6 place-items-center rounded text-ink-faint hover:bg-bad/15 hover:text-bad">
                <Trash2 size={11} />
              </button>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function Tester() {
  const pushToast = useApp((s) => s.pushToast);
  const preferred = useApp((s) => s.settings?.tts_engine ?? "piper");
  const [text, setText] = useState("Backend'i FastAPI ile yazdım, frontend'de React ve TypeScript kullanıyorum.");
  const [result, setResult] = useState<Record<string, string> | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const run = async (speak: boolean, engine?: "piper" | "macos") => {
    setBusy(speak ? (engine ?? "x") : "show");
    try {
      const r = await api.testPronunciation(text, engine, speak);
      setResult(r.spoken);
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setBusy(null);
    }
  };
  return (
    <div className="rounded-md border border-white/[0.06] bg-white/[0.015] p-3">
      <div className="hud-label mb-2 !text-[9px] accent-text">Telaffuzu dene</div>
      <textarea value={text} rows={2} onChange={(e) => setText(e.target.value)} className={`${inputCls} w-full resize-none`} />
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        <button type="button" onClick={() => void run(false)} className="rounded-md border border-white/[0.08] px-2.5 py-1 text-[11.5px] text-ink-dim hover:text-white">
          {busy === "show" ? <Loader2 size={11} className="animate-spin" /> : "Okunuşu göster"}
        </button>
        {(["piper", "macos"] as const).map((engine) => (
          <button
            key={engine}
            type="button"
            disabled={busy !== null}
            onClick={() => void run(true, engine)}
            className={`flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-[11.5px] hover:text-white disabled:opacity-50 ${engine === preferred ? "accent-border text-ink" : "border-white/[0.08] text-ink-dim"}`}
          >
            {busy === engine ? <Loader2 size={11} className="animate-spin" /> : <Volume2 size={11} />} {engine === "piper" ? "Piper ile dinle" : "Yelda ile dinle"}
          </button>
        ))}
      </div>
      {result && (
        <div className="mt-2.5 space-y-1 font-mono text-[11px] text-ink-dim">
          {Object.entries(result).map(([engine, spoken]) => (
            <div key={engine}>
              <span className="accent-text">{engine === "piper" ? "Piper" : "Yelda"}:</span> {spoken}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function PronunciationPanel() {
  const setUi = useApp((s) => s.setUi);
  const pushToast = useApp((s) => s.pushToast);
  const settings = useApp((s) => s.settings);
  const update = useApp((s) => s.updateSettings);
  const [data, setData] = useState<PronunciationOverview | null>(null);
  const [draft, setDraft] = useState<Partial<PronunciationRule> | null>(null);

  const load = useCallback(() => {
    api.pronunciation().then(setData).catch((e) => pushToast("error", (e as Error).message));
  }, [pushToast]);
  useEffect(load, [load]);

  const overridden = useMemo(() => new Set(data?.custom.map((r) => `${r.term.toLocaleLowerCase("tr")}|${r.engine}`) ?? []), [data]);

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="absolute inset-0 z-50 flex items-center justify-center bg-void/55 p-6 backdrop-blur-sm"
      onClick={() => setUi({ pronunciationOpen: false })}
    >
      <motion.div
        initial={{ y: 14, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        exit={{ y: 10, opacity: 0 }}
        transition={{ duration: 0.3, ease: [0.2, 0.8, 0.2, 1] }}
        onClick={(e) => e.stopPropagation()}
        className="glass-strong brackets relative flex h-[min(760px,100%)] w-[820px] max-w-full flex-col overflow-hidden rounded-xl"
      >
        <header className="flex h-12 shrink-0 items-center gap-3 border-b border-white/[0.06] px-5">
          <span className="hud-label flex-1 accent-text">Telaffuz sözlüğü</span>
          <label className="flex items-center gap-2 text-[11.5px] text-ink-dim">
            Düzeltmeler
            <button
              type="button"
              role="switch"
              aria-checked={settings?.pronunciation_enabled ?? true}
              onClick={() => settings && void update({ pronunciation_enabled: !settings.pronunciation_enabled })}
              className="relative h-5 w-9 rounded-full transition-colors"
              style={{ background: settings?.pronunciation_enabled ? "var(--accent)" : "rgb(255 255 255 / 0.12)" }}
            >
              <span className="absolute top-0.5 h-4 w-4 rounded-full bg-white transition-all" style={{ left: settings?.pronunciation_enabled ? 18 : 2 }} />
            </button>
          </label>
          <button type="button" onClick={() => setUi({ pronunciationOpen: false })} className="grid h-7 w-7 place-items-center rounded-md text-ink-dim hover:bg-white/[0.06] hover:text-white" aria-label="Kapat">
            <X size={14} />
          </button>
        </header>
        <div className="scroll-thin flex-1 overflow-y-auto px-5 py-4">
          <p className="mb-4 text-[12px] leading-relaxed text-ink-dim">
            Kurallar yalnızca sese giden metni değiştirir; ekrandaki yanıt, konuşma geçmişi ve hafıza olduğu gibi kalır. Bağlantılar, dosya
            yolları ve komutlar korunur. Aynı yazım iki seste farklı okunabildiği için kurallar sese özel olabilir.
          </p>
          <Tester />
          <div className="mb-2 mt-5 flex items-center gap-2">
            <span className="hud-label !text-[9px] accent-text">Senin kuralların</span>
            <span className="h-px flex-1 bg-gradient-to-r from-white/10 to-transparent" />
          </div>
          <div className="mb-3">
            <RuleForm
              key={draft ? `${draft.term}-${draft.engine}` : "new"}
              initial={draft ?? undefined}
              submitLabel="Ekle"
              onSubmit={async (r) => {
                try {
                  await api.addPronunciation(r);
                  setDraft(null);
                  load();
                } catch (e) {
                  pushToast("error", (e as Error).message);
                }
              }}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            {data?.custom.length === 0 && <p className="py-3 text-center text-[12px] text-ink-faint">Henüz kendi kuralın yok.</p>}
            {data?.custom.map((r) => <RuleRow key={r.id} rule={r} onChanged={load} onCustomize={setDraft} />)}
          </div>
          <div className="mb-2 mt-6 flex items-center gap-2">
            <span className="hud-label !text-[9px] accent-text">Hazır kurallar</span>
            <span className="h-px flex-1 bg-gradient-to-r from-white/10 to-transparent" />
            <span className="font-mono text-[9.5px] text-ink-faint">Whisper geri-dinleme testiyle seçildi</span>
          </div>
          <div className="flex flex-col gap-1.5">
            {data?.builtin.map((r) => (
              <RuleRow
                key={r.id}
                rule={{ ...r, enabled: !overridden.has(`${r.term.toLocaleLowerCase("tr")}|${r.engine}`) && !overridden.has(`${r.term.toLocaleLowerCase("tr")}|all`) }}
                onChanged={load}
                onCustomize={(rule) => setDraft({ term: rule.term, replacement: rule.replacement, engine: rule.engine, case_sensitive: rule.case_sensitive })}
              />
            ))}
          </div>
          {!data && (
            <div className="grid place-items-center py-10">
              <Loader2 size={16} className="animate-spin accent-text" />
            </div>
          )}
        </div>
      </motion.div>
    </motion.div>
  );
}
