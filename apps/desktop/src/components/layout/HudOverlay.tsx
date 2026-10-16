import { AnimatePresence, motion } from "motion/react";
import { useEffect, useRef } from "react";

import { useApp } from "../../stores/app";
import { currentLevel } from "../../stores/audio";
import { formatMs } from "../../utils/format";
import { stateLabel } from "../ai-core/palette";

/** Circular technical markings around the core (pure SVG/CSS — no per-frame React work). */
function Reticle() {
  const ticks = Array.from({ length: 120 }, (_, i) => i);
  return (
    <svg className="pointer-events-none absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2" width="760" height="760" viewBox="-380 -380 760 760" aria-hidden>
      <g className="animate-spin-slow" style={{ transformOrigin: "0 0" }}>
        {ticks.map((i) => {
          const a = (i / ticks.length) * Math.PI * 2;
          const major = i % 10 === 0;
          const r1 = major ? 338 : 344;
          return (
            <line
              key={i}
              x1={Math.cos(a) * r1}
              y1={Math.sin(a) * r1}
              x2={Math.cos(a) * 350}
              y2={Math.sin(a) * 350}
              stroke="var(--accent)"
              strokeOpacity={major ? 0.45 : 0.16}
              strokeWidth={major ? 1.2 : 0.8}
            />
          );
        })}
        {[0, 90, 180, 270].map((deg) => (
          <text
            key={deg}
            transform={`rotate(${deg}) translate(0 -360)`}
            textAnchor="middle"
            className="fill-[var(--accent)] font-mono"
            style={{ fontSize: 8, letterSpacing: "0.25em", opacity: 0.5 }}
          >
            {["LZ-01 · NEURAL CORE", "LOCAL INFERENCE", "TR · EN", "SECURE · OFFLINE"][deg / 90]}
          </text>
        ))}
      </g>
      <g className="animate-spin-rev" style={{ transformOrigin: "0 0" }}>
        <circle r="318" fill="none" stroke="var(--accent)" strokeOpacity="0.12" strokeDasharray="2 10" />
        <path d="M -300 0 A 300 300 0 0 1 -212 -212" fill="none" stroke="var(--accent)" strokeOpacity="0.5" strokeWidth="1.5" />
        <path d="M 300 0 A 300 300 0 0 1 212 212" fill="none" stroke="var(--accent)" strokeOpacity="0.5" strokeWidth="1.5" />
      </g>
      <circle r="372" fill="none" stroke="var(--accent)" strokeOpacity="0.06" />
    </svg>
  );
}

function Readout({ label, value, unit }: { label: string; value: string; unit?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-6 py-[3px]">
      <span className="hud-label !text-[9px] whitespace-nowrap">{label}</span>
      <span className="whitespace-nowrap font-mono text-[11px] text-ink">
        {value}
        {unit && <span className="ml-1 text-ink-faint">{unit}</span>}
      </span>
    </div>
  );
}

function Telemetry() {
  const system = useApp((s) => s.system);
  const turn = useApp((s) => s.lastTurn);
  const settings = useApp((s) => s.settings);
  return (
    <>
      <div className="brackets absolute left-6 top-6 w-64 px-3 py-2.5">
        <div className="hud-label mb-1.5 accent-text">Sistem</div>
        <Readout label="Model" value={settings?.llm_model ?? "—"} />
        <Readout label="Konuşma tanıma" value={system?.stt.available ? "whisper-turbo" : "yok"} />
        <Readout label="Ses" value={system?.tts.preferred === "macos" ? "macOS Yelda" : "Piper TR"} />
        <Readout label="Bağlam" value={settings ? `${(settings.num_ctx / 1024).toFixed(0)}K` : "—"} unit="token" />
        <Readout label="Araştırma" value={settings?.research_mode === "off" ? "kapalı" : settings?.research_mode === "always" ? "her zaman" : "otomatik"} />
      </div>
      <div className="brackets absolute right-6 top-6 w-60 px-3 py-2.5">
        <div className="hud-label mb-1.5 accent-text">Son tur</div>
        <Readout label="İlk token" value={formatMs(turn?.ttft_ms)} />
        <Readout label="Üretim hızı" value={turn?.tokens_per_s ? turn.tokens_per_s.toFixed(1) : "—"} unit="tok/s" />
        <Readout label="Ses tanıma" value={formatMs(turn?.stt_ms)} />
        <Readout label="Araştırma" value={formatMs(turn?.research_ms)} />
        <Readout label="İlk ses" value={formatMs(turn?.tts_first_audio_ms)} />
      </div>
    </>
  );
}

/** Live audio meters updated with direct DOM writes from requestAnimationFrame. */
function AudioMeters() {
  const micRef = useRef<HTMLDivElement>(null);
  const ttsRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let raf = 0;
    const bars = 24;
    const render = () => {
      for (const [el, src] of [
        [micRef.current, "mic"],
        [ttsRef.current, "tts"],
      ] as const) {
        if (!el) continue;
        const lvl = currentLevel(src);
        const lit = Math.round(lvl * bars);
        const children = el.children;
        for (let i = 0; i < children.length; i++) {
          (children[i] as HTMLElement).style.opacity = i < lit ? "1" : "0.14";
        }
      }
      raf = requestAnimationFrame(render);
    };
    raf = requestAnimationFrame(render);
    return () => cancelAnimationFrame(raf);
  }, []);
  const Bar = ({ refEl }: { refEl: React.RefObject<HTMLDivElement | null> }) => (
    <div ref={refEl} className="flex h-2.5 items-end gap-[3px]">
      {Array.from({ length: 24 }, (_, i) => (
        <div
          key={i}
          className="w-[3px] rounded-[1px] transition-opacity duration-75"
          style={{ height: `${40 + (i / 24) * 60}%`, background: "var(--accent)", opacity: 0.14 }}
        />
      ))}
    </div>
  );
  return (
    <div className="brackets absolute bottom-28 left-6 w-56 px-3 py-2.5">
      <div className="hud-label mb-2 accent-text">Ses akışı</div>
      <div className="mb-2 flex items-center justify-between gap-3">
        <span className="hud-label !text-[9px]">Mikrofon</span>
        <Bar refEl={micRef} />
      </div>
      <div className="flex items-center justify-between gap-3">
        <span className="hud-label !text-[9px]">LAZOS</span>
        <Bar refEl={ttsRef} />
      </div>
    </div>
  );
}

function Caption() {
  const caption = useApp((s) => s.caption);
  const aiState = useApp((s) => s.aiState);
  const chatOpen = useApp((s) => s.ui.chatOpen);
  const show = caption && (!chatOpen || aiState === "speaking" || aiState === "transcribing" || aiState === "thinking");
  return (
    <div className="pointer-events-none absolute inset-x-0 bottom-[98px] flex justify-center px-16">
      <AnimatePresence mode="wait">
        {show && (
          <motion.p
            key={caption.slice(0, 24)}
            initial={{ opacity: 0, y: 6, filter: "blur(4px)" }}
            animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
            exit={{ opacity: 0, y: -4, filter: "blur(4px)" }}
            transition={{ duration: 0.35 }}
            className="max-w-2xl text-center text-[15px] leading-relaxed text-ink/90 [text-shadow:0_2px_20px_rgb(0_0_0/0.9)]"
          >
            {caption}
          </motion.p>
        )}
      </AnimatePresence>
    </div>
  );
}

export function HudOverlay() {
  const aiState = useApp((s) => s.aiState);
  const aiDetail = useApp((s) => s.aiDetail);
  const conn = useApp((s) => s.conn);
  const userSpeaking = useApp((s) => s.userSpeaking);
  const label = conn === "online" ? stateLabel(aiState, aiDetail) : "BAĞLANIYOR";
  return (
    <div className="pointer-events-none absolute inset-0 select-none">
      <Reticle />
      <div className="absolute left-1/2 top-1/2 mt-[250px] -translate-x-1/2 text-center">
        <AnimatePresence mode="wait">
          <motion.div
            key={label}
            initial={{ opacity: 0, letterSpacing: "0.6em" }}
            animate={{ opacity: 1, letterSpacing: "0.45em" }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.5 }}
            className="font-display text-[13px] font-medium accent-text accent-glow"
          >
            {label}
          </motion.div>
        </AnimatePresence>
        <div className="mt-1 font-mono text-[9.5px] tracking-[0.3em] text-ink-faint">
          {aiState === "listening" ? (userSpeaking ? "SES ALGILANDI" : "SENİ DİNLİYORUM") : "LAZOS · YEREL ZEKÂ"}
        </div>
      </div>
      <Telemetry />
      <AudioMeters />
      <Caption />
    </div>
  );
}
