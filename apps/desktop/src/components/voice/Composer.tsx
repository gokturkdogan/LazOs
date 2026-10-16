import { ArrowUp, AudioLines, Mic, Square, VolumeX } from "lucide-react";
import { motion } from "motion/react";
import { useEffect, useRef, useState } from "react";

import { useApp } from "../../stores/app";
import { currentLevel } from "../../stores/audio";

/** Microphone button with a halo that follows the real microphone level. */
function MicButton() {
  const aiState = useApp((s) => s.aiState);
  const voiceMode = useApp((s) => s.voiceMode);
  const conn = useApp((s) => s.conn);
  const toggleMic = useApp((s) => s.toggleMic);
  const halo = useRef<HTMLSpanElement>(null);
  const listening = aiState === "listening";

  useEffect(() => {
    if (!listening) return;
    let raf = 0;
    let smooth = 0;
    const tick = () => {
      smooth += (currentLevel("mic") - smooth) * 0.35;
      if (halo.current) {
        halo.current.style.transform = `scale(${1 + smooth * 0.9})`;
        halo.current.style.opacity = String(0.25 + smooth * 0.75);
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [listening]);

  const label =
    voiceMode === "continuous"
      ? "Sesli moddayken dokun: LAZOS'u kes ve dinlemeye dön"
      : listening
        ? "Konuşmayı bitir"
        : "Konuşmak için dokun";

  return (
    <button
      type="button"
      onClick={toggleMic}
      disabled={conn !== "online"}
      title={label}
      aria-label={label}
      aria-pressed={listening}
      className="relative grid h-11 w-11 shrink-0 place-items-center rounded-full border transition-colors disabled:opacity-40"
      style={{
        borderColor: listening ? "var(--accent)" : "rgb(140 190 255 / 0.18)",
        background: listening ? "color-mix(in srgb, var(--accent) 22%, transparent)" : "rgb(255 255 255 / 0.03)",
      }}
    >
      {listening && (
        <span
          ref={halo}
          className="pointer-events-none absolute inset-0 rounded-full"
          style={{ boxShadow: "0 0 0 2px var(--accent), 0 0 24px var(--accent)", transition: "transform 60ms linear" }}
        />
      )}
      <Mic size={18} className={listening ? "text-white" : "text-ink"} />
    </button>
  );
}

function VoiceModeToggle() {
  const voiceMode = useApp((s) => s.voiceMode);
  const conn = useApp((s) => s.conn);
  const setContinuous = useApp((s) => s.setContinuous);
  const on = voiceMode === "continuous";
  return (
    <button
      type="button"
      onClick={() => setContinuous(!on)}
      disabled={conn !== "online"}
      title={on ? "Sesli konuşma modunu kapat" : "Sesli konuşma modu: elleri serbest, sürekli dinler"}
      aria-pressed={on}
      className="flex h-11 shrink-0 items-center gap-2 rounded-full border px-3.5 transition-colors disabled:opacity-40"
      style={{
        borderColor: on ? "var(--accent)" : "rgb(140 190 255 / 0.14)",
        background: on ? "color-mix(in srgb, var(--accent) 16%, transparent)" : "transparent",
      }}
    >
      <AudioLines size={15} className={on ? "accent-text" : "text-ink-dim"} />
      <span className={`font-display text-[10px] font-medium tracking-[0.22em] ${on ? "text-white" : "text-ink-dim"}`}>
        SESLİ MOD
      </span>
      <span
        className="relative h-3.5 w-6 rounded-full transition-colors"
        style={{ background: on ? "var(--accent)" : "rgb(255 255 255 / 0.12)" }}
      >
        <motion.span
          className="absolute top-0.5 h-2.5 w-2.5 rounded-full bg-white"
          animate={{ left: on ? 12 : 2 }}
          transition={{ type: "spring", stiffness: 500, damping: 32 }}
        />
      </span>
    </button>
  );
}

export function Composer() {
  const [text, setText] = useState("");
  const area = useRef<HTMLTextAreaElement>(null);
  const conn = useApp((s) => s.conn);
  const streamingId = useApp((s) => s.streamingId);
  const aiState = useApp((s) => s.aiState);
  const send = useApp((s) => s.sendMessage);
  const cancel = useApp((s) => s.cancel);
  const stopSpeaking = useApp((s) => s.stopSpeaking);
  const modelReady = useApp((s) => s.models?.find((m) => m.id === "llm")?.status === "present");

  const busy = Boolean(streamingId) || aiState === "thinking";
  const speaking = aiState === "speaking";
  const disabled = conn !== "online" || modelReady === false;

  useEffect(() => {
    const el = area.current;
    if (!el) return;
    el.style.height = "0px";
    el.style.height = `${Math.min(140, el.scrollHeight)}px`;
  }, [text]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        if (busy) cancel();
        else if (speaking) stopSpeaking();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, speaking, cancel, stopSpeaking]);

  const submit = () => {
    if (!text.trim() || disabled) return;
    send(text);
    setText("");
  };

  return (
    <div className="pointer-events-none absolute inset-x-0 bottom-5 z-20 flex justify-center px-6">
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, delay: 0.15 }}
        className="glass pointer-events-auto flex w-full max-w-[760px] items-end gap-2 rounded-[28px] p-2"
      >
        <VoiceModeToggle />
        <div className="flex min-h-11 flex-1 items-center">
          <textarea
            ref={area}
            value={text}
            rows={1}
            disabled={disabled}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                submit();
              }
            }}
            placeholder={disabled ? (conn === "online" ? "Dil modeli hazır değil — kurulumu tamamla" : "Yerel servise bağlanılıyor…") : "LAZOS'a yaz…"}
            className="selectable w-full resize-none bg-transparent px-2 py-2.5 text-[14px] leading-relaxed text-ink outline-none placeholder:text-ink-faint"
          />
        </div>
        <MicButton />
        {busy ? (
          <button
            type="button"
            onClick={cancel}
            title="Yanıtı durdur (Esc)"
            className="grid h-11 w-11 shrink-0 place-items-center rounded-full border border-white/15 bg-white/[0.06] text-white transition-colors hover:bg-white/[0.12]"
          >
            <Square size={14} fill="currentColor" />
          </button>
        ) : speaking ? (
          <button
            type="button"
            onClick={stopSpeaking}
            title="Konuşmayı durdur (Esc)"
            className="grid h-11 w-11 shrink-0 place-items-center rounded-full border border-white/15 bg-white/[0.06] text-white transition-colors hover:bg-white/[0.12]"
          >
            <VolumeX size={16} />
          </button>
        ) : (
          <button
            type="button"
            onClick={submit}
            disabled={!text.trim() || disabled}
            title="Gönder (Enter)"
            className="grid h-11 w-11 shrink-0 place-items-center rounded-full transition-all disabled:opacity-30"
            style={{ background: "var(--accent)", boxShadow: text.trim() ? "0 0 22px color-mix(in srgb, var(--accent) 55%, transparent)" : "none" }}
          >
            <ArrowUp size={18} className="text-void" strokeWidth={2.5} />
          </button>
        )}
      </motion.div>
    </div>
  );
}
