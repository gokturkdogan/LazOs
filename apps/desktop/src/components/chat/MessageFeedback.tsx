import { Check, PenLine, ThumbsDown, ThumbsUp, X } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useState } from "react";

import { useApp } from "../../stores/app";
import type { ChatMessage, FeedbackReason } from "../../types/protocol";

const REASONS: [FeedbackReason, string][] = [
  ["incorrect", "Yanlış bilgi"],
  ["unnatural_turkish", "Doğal olmayan Türkçe"],
  ["too_long", "Çok uzun"],
  ["too_short", "Çok kısa"],
  ["misunderstood", "Yanlış anladı"],
  ["lost_context", "Bağlamı kaybetti"],
  ["wrong_tone", "Yanlış üslup"],
  ["unnecessary_tool", "Gereksiz arama"],
  ["other", "Diğer"],
];

const inputCls =
  "selectable w-full rounded-md border border-white/[0.08] bg-abyss px-2.5 py-1.5 text-[12.5px] text-ink outline-none focus:border-[color-mix(in_srgb,var(--accent)_45%,transparent)]";

function IconToggle({ active, title, onClick, children }: { active?: boolean; title: string; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      title={title}
      aria-label={title}
      aria-pressed={active}
      onClick={onClick}
      className={`grid h-6 w-6 place-items-center rounded transition-colors ${active ? "accent-text bg-white/[0.07]" : "text-ink-faint hover:bg-white/[0.06] hover:text-white"}`}
    >
      {children}
    </button>
  );
}

/** Subtle per-answer feedback: rate, explain what went wrong, or write the answer you wanted. */
export function AnswerFeedback({ message }: { message: ChatMessage }) {
  const rate = useApp((s) => s.rateMessage);
  const withdraw = useApp((s) => s.withdrawRating);
  const correct = useApp((s) => s.correctMessage);
  const [panel, setPanel] = useState<"reasons" | "correct" | null>(null);
  const [reasons, setReasons] = useState<FeedbackReason[]>(message.feedback?.reasons ?? []);
  const [note, setNote] = useState(message.feedback?.note ?? "");
  const [preferred, setPreferred] = useState(message.correction?.preferred_response ?? "");
  const [asExample, setAsExample] = useState(true);
  const rating = message.feedback?.rating;

  const thumb = (value: 1 | -1) => {
    if (rating === value) {
      void withdraw(message.id);
      setPanel(null);
    } else if (value === 1) {
      void rate(message.id, 1);
      setPanel(null);
    } else {
      void rate(message.id, -1, reasons, note);
      setPanel("reasons");
    }
  };

  return (
    <div className="mt-2">
      <div className={`flex items-center gap-0.5 transition-opacity ${rating || panel || message.correction ? "opacity-100" : "opacity-0 group-hover/answer:opacity-100"}`}>
        <IconToggle active={rating === 1} title="Faydalıydı" onClick={() => thumb(1)}>
          <ThumbsUp size={12} fill={rating === 1 ? "currentColor" : "none"} />
        </IconToggle>
        <IconToggle active={rating === -1} title="Beğenmedim" onClick={() => thumb(-1)}>
          <ThumbsDown size={12} fill={rating === -1 ? "currentColor" : "none"} />
        </IconToggle>
        <IconToggle active={panel === "correct" || Boolean(message.correction)} title="Doğru cevabı yaz" onClick={() => setPanel(panel === "correct" ? null : "correct")}>
          <PenLine size={12} />
        </IconToggle>
        {message.correction && panel !== "correct" && (
          <span className="ml-1 font-mono text-[9.5px] text-ink-faint">
            {message.correction.usable_as_example
              ? "düzeltme · örnek olarak kullanılıyor"
              : message.correction.kind === "factual"
                ? "bilgi düzeltmesi · örnek değil"
                : "düzeltme kaydedildi"}
          </span>
        )}
      </div>

      <AnimatePresence initial={false}>
        {panel === "reasons" && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            className="overflow-hidden"
          >
            <div className="mt-1.5 rounded-md border border-white/[0.07] bg-white/[0.02] p-2.5">
              <div className="mb-2 flex flex-wrap gap-1">
                {REASONS.map(([key, label]) => {
                  const on = reasons.includes(key);
                  return (
                    <button
                      key={key}
                      type="button"
                      onClick={() => setReasons(on ? reasons.filter((r) => r !== key) : [...reasons, key])}
                      className={`rounded-full border px-2 py-0.5 text-[10.5px] transition-colors ${on ? "accent-border bg-white/[0.08] text-white" : "border-white/[0.08] text-ink-dim hover:text-white"}`}
                    >
                      {label}
                    </button>
                  );
                })}
              </div>
              <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} placeholder="İsteğe bağlı not…" className={inputCls} />
              <div className="mt-2 flex justify-end gap-1.5">
                <button type="button" onClick={() => setPanel(null)} className="px-2 text-[11px] text-ink-dim hover:text-white">
                  Kapat
                </button>
                <button
                  type="button"
                  onClick={() => {
                    void rate(message.id, -1, reasons, note);
                    setPanel(null);
                  }}
                  className="flex items-center gap-1 rounded-md border accent-border px-2.5 py-1 text-[11px] text-white"
                >
                  <Check size={11} /> Kaydet
                </button>
              </div>
            </div>
          </motion.div>
        )}
        {panel === "correct" && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            className="overflow-hidden"
          >
            <div className="mt-1.5 rounded-md border border-white/[0.07] bg-white/[0.02] p-2.5">
              <div className="hud-label mb-1.5 !text-[9px]">Bu soruya nasıl cevap vermesini isterdin?</div>
              <textarea value={preferred} rows={3} onChange={(e) => setPreferred(e.target.value)} className={`${inputCls} resize-none`} />
              <label className="mt-2 flex items-center gap-1.5 text-[11px] text-ink-dim">
                <input type="checkbox" checked={asExample} onChange={(e) => setAsExample(e.target.checked)} className="accent-[var(--accent)]" />
                Benzer sorularda örnek olarak kullan (bilgi düzeltmeleri hariç)
              </label>
              <div className="mt-2 flex justify-end gap-1.5">
                <button type="button" onClick={() => setPanel(null)} className="flex items-center gap-1 px-2 text-[11px] text-ink-dim hover:text-white">
                  <X size={11} /> Vazgeç
                </button>
                <button
                  type="button"
                  disabled={preferred.trim().length < 2}
                  onClick={() => void correct(message.id, preferred.trim(), asExample).then((ok) => ok && setPanel(null))}
                  className="flex items-center gap-1 rounded-md px-2.5 py-1 text-[11px] font-medium text-void disabled:opacity-30"
                  style={{ background: "var(--accent)" }}
                >
                  <Check size={11} /> Kaydet
                </button>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

/** Lets the user fix a misrecognised voice message; the original Whisper text is kept. */
export function TranscriptEditor({ message, onDone }: { message: ChatMessage; onDone: () => void }) {
  const correct = useApp((s) => s.correctTranscript);
  const [value, setValue] = useState(message.content);
  return (
    <form
      className="w-full max-w-[88%]"
      onSubmit={(e) => {
        e.preventDefault();
        if (value.trim() && value.trim() !== message.content) void correct(message.id, value.trim()).then((ok) => ok && onDone());
        else onDone();
      }}
    >
      <textarea autoFocus value={value} rows={2} onChange={(e) => setValue(e.target.value)} className={`${inputCls} resize-none`} />
      <div className="mt-1 flex items-center justify-end gap-1.5">
        <span className="mr-auto text-[10.5px] text-ink-faint">Ses tanıma düzeltmesi · orijinal metin saklanır</span>
        <button type="button" onClick={onDone} className="px-2 text-[11px] text-ink-dim hover:text-white">
          Vazgeç
        </button>
        <button type="submit" className="flex items-center gap-1 rounded-md border accent-border px-2.5 py-1 text-[11px] text-white">
          <Check size={11} /> Düzelt
        </button>
      </div>
    </form>
  );
}
