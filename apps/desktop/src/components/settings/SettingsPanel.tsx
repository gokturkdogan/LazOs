import { HardDrive, Loader2, RefreshCw, Volume2, X } from "lucide-react";
import { motion } from "motion/react";
import { useEffect, useState } from "react";

import { api } from "../../services/api";
import { useApp } from "../../stores/app";
import type { AudioDevice, UserSettings } from "../../types/protocol";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mb-6">
      <div className="mb-3 flex items-center gap-2">
        <span className="hud-label accent-text">{title}</span>
        <span className="h-px flex-1 bg-gradient-to-r from-white/10 to-transparent" />
      </div>
      <div className="flex flex-col gap-3.5">{children}</div>
    </section>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4">
      <div className="min-w-0">
        <div className="text-[12.5px] text-ink">{label}</div>
        {hint && <div className="mt-0.5 text-[11px] leading-snug text-ink-faint">{hint}</div>}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}

function Segmented<T extends string>({ value, options, onChange }: { value: T; options: [T, string][]; onChange: (v: T) => void }) {
  return (
    <div className="flex rounded-md border border-white/[0.08] bg-white/[0.02] p-0.5">
      {options.map(([v, label]) => (
        <button
          key={v}
          type="button"
          onClick={() => onChange(v)}
          className={`rounded px-2.5 py-1 text-[11px] transition-colors ${
            value === v ? "bg-white/[0.1] text-white" : "text-ink-dim hover:text-white"
          }`}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

function Toggle({ value, onChange }: { value: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={value}
      onClick={() => onChange(!value)}
      className="relative h-5 w-9 rounded-full transition-colors"
      style={{ background: value ? "var(--accent)" : "rgb(255 255 255 / 0.12)" }}
    >
      <span className="absolute top-0.5 h-4 w-4 rounded-full bg-white transition-all" style={{ left: value ? 18 : 2 }} />
    </button>
  );
}

function Slider({
  value,
  min,
  max,
  step,
  format,
  onChange,
}: {
  value: number;
  min: number;
  max: number;
  step: number;
  format: (v: number) => string;
  onChange: (v: number) => void;
}) {
  const [local, setLocal] = useState(value);
  useEffect(() => setLocal(value), [value]);
  return (
    <div className="flex items-center gap-2.5">
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={local}
        onChange={(e) => setLocal(Number(e.target.value))}
        onPointerUp={() => local !== value && onChange(local)}
        onKeyUp={() => local !== value && onChange(local)}
        className="w-32 accent-[var(--accent)]"
      />
      <span className="w-12 text-right font-mono text-[11px] text-ink">{format(local)}</span>
    </div>
  );
}

function DeviceSelect({ value, devices, onChange }: { value: string | null; devices: AudioDevice[]; onChange: (v: string | null) => void }) {
  return (
    <select
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value || null)}
      className="w-52 rounded-md border border-white/[0.08] bg-abyss px-2 py-1.5 text-[11.5px] text-ink outline-none"
    >
      <option value="">Sistem varsayılanı</option>
      {devices.map((d) => (
        <option key={d.index} value={d.name}>
          {d.name}
        </option>
      ))}
    </select>
  );
}

export function SettingsPanel() {
  const settings = useApp((s) => s.settings);
  const update = useApp((s) => s.updateSettings);
  const setUi = useApp((s) => s.setUi);
  const pushToast = useApp((s) => s.pushToast);
  const [devices, setDevices] = useState<{ inputs: AudioDevice[]; outputs: AudioDevice[] }>({ inputs: [], outputs: [] });
  const [previewing, setPreviewing] = useState<string | null>(null);
  const [modelDraft, setModelDraft] = useState(settings?.llm_model ?? "");

  const loadDevices = (refresh = false) =>
    api
      .devices(refresh)
      .then(setDevices)
      .catch((e) => pushToast("error", `Ses aygıtları alınamadı: ${(e as Error).message}`));

  useEffect(() => {
    void loadDevices();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => setModelDraft(settings?.llm_model ?? ""), [settings?.llm_model]);

  if (!settings) return null;
  const set = <K extends keyof UserSettings>(key: K, value: UserSettings[K]) => void update({ [key]: value });

  const preview = async (engine: "piper" | "macos") => {
    setPreviewing(engine);
    try {
      await api.previewVoice(engine);
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setPreviewing(null);
    }
  };

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="absolute inset-0 z-40 flex justify-end bg-void/40"
      onClick={() => setUi({ settingsOpen: false })}
    >
      <motion.aside
        initial={{ x: 40, opacity: 0 }}
        animate={{ x: 0, opacity: 1 }}
        exit={{ x: 40, opacity: 0 }}
        transition={{ duration: 0.3, ease: [0.2, 0.8, 0.2, 1] }}
        onClick={(e) => e.stopPropagation()}
        className="glass-strong m-3 flex w-[460px] flex-col overflow-hidden rounded-xl"
      >
        <header className="flex h-12 shrink-0 items-center border-b border-white/[0.06] px-5">
          <span className="hud-label flex-1 accent-text">Ayarlar</span>
          <button type="button" onClick={() => setUi({ settingsOpen: false })} className="grid h-7 w-7 place-items-center rounded-md text-ink-dim hover:bg-white/[0.06] hover:text-white" aria-label="Kapat">
            <X size={14} />
          </button>
        </header>
        <div className="scroll-thin flex-1 overflow-y-auto px-5 py-5">
          <Section title="Dil modeli">
            <Field label="Ollama modeli" hint="Yerel olarak yüklü bir Ollama model etiketi.">
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  if (modelDraft.trim() && modelDraft !== settings.llm_model) set("llm_model", modelDraft.trim());
                }}
              >
                <input
                  value={modelDraft}
                  onChange={(e) => setModelDraft(e.target.value)}
                  onBlur={() => modelDraft.trim() && modelDraft !== settings.llm_model && set("llm_model", modelDraft.trim())}
                  className="selectable w-40 rounded-md border border-white/[0.08] bg-abyss px-2 py-1.5 font-mono text-[11.5px] text-ink outline-none"
                />
              </form>
            </Field>
            <Field label="Yaratıcılık" hint="Düşük değerler daha tutarlı, yüksek değerler daha çeşitli yanıtlar verir.">
              <Slider value={settings.temperature} min={0} max={1.2} step={0.05} format={(v) => v.toFixed(2)} onChange={(v) => set("temperature", v)} />
            </Field>
            <Field label="Bağlam penceresi" hint="Modele gönderilen konuşma geçmişi bütçesi. Eski mesajlar otomatik özetlenir.">
              <Segmented
                value={String(settings.num_ctx)}
                options={[
                  ["4096", "4K"],
                  ["8192", "8K"],
                  ["16384", "16K"],
                ]}
                onChange={(v) => set("num_ctx", Number(v))}
              />
            </Field>
            <Field label="Modeli bellekte tut" hint="Hızlı yanıt için modelin yüklü kalacağı süre.">
              <Segmented
                value={String(settings.keep_alive_minutes)}
                options={[
                  ["5", "5 dk"],
                  ["30", "30 dk"],
                  ["240", "4 sa"],
                ]}
                onChange={(v) => set("keep_alive_minutes", Number(v))}
              />
            </Field>
          </Section>

          <Section title="Ses">
            <Field label="Ses motoru" hint="Piper: çok hızlı nöral ses. Yelda: macOS'un yerleşik Türkçe sesi, daha net telaffuz.">
              <Segmented
                value={settings.tts_engine}
                options={[
                  ["piper", "Piper"],
                  ["macos", "Yelda"],
                ]}
                onChange={(v) => set("tts_engine", v)}
              />
            </Field>
            <Field label="Sesi dene">
              <div className="flex gap-1.5">
                {(["piper", "macos"] as const).map((engine) => (
                  <button
                    key={engine}
                    type="button"
                    disabled={previewing !== null}
                    onClick={() => void preview(engine)}
                    className="flex items-center gap-1.5 rounded-md border border-white/[0.08] px-2.5 py-1 text-[11px] text-ink-dim transition-colors hover:text-white disabled:opacity-50"
                  >
                    {previewing === engine ? <Loader2 size={11} className="animate-spin" /> : <Volume2 size={11} />}
                    {engine === "piper" ? "Piper" : "Yelda"}
                  </button>
                ))}
              </div>
            </Field>
            <Field label="Konuşma hızı">
              <Slider value={settings.tts_speed} min={0.7} max={1.4} step={0.05} format={(v) => `${v.toFixed(2)}×`} onChange={(v) => set("tts_speed", v)} />
            </Field>
            <Field label="Yazılı yanıtları da seslendir" hint="Kapalıyken yalnızca sesli konuşmalar seslendirilir.">
              <Toggle value={settings.speak_text_replies} onChange={(v) => set("speak_text_replies", v)} />
            </Field>
            <Field label="Konuşma tanıma dili">
              <Segmented
                value={settings.stt_language}
                options={[
                  ["auto", "Otomatik"],
                  ["tr", "Türkçe"],
                  ["en", "English"],
                ]}
                onChange={(v) => set("stt_language", v)}
              />
            </Field>
            <Field label="Ses algılama hassasiyeti" hint="Gürültülü ortamda yükselt.">
              <Slider value={settings.vad_threshold} min={0.3} max={0.85} step={0.05} format={(v) => v.toFixed(2)} onChange={(v) => set("vad_threshold", v)} />
            </Field>
            <Field label="Konuşma sonu bekleme" hint="Sessizlik bu süreyi aşınca sözünün bittiği kabul edilir.">
              <Slider value={settings.vad_silence_ms} min={400} max={1800} step={50} format={(v) => `${v} ms`} onChange={(v) => set("vad_silence_ms", v)} />
            </Field>
            <Field label="Mikrofon">
              <DeviceSelect value={settings.input_device} devices={devices.inputs} onChange={(v) => set("input_device", v)} />
            </Field>
            <Field label="Hoparlör">
              <DeviceSelect value={settings.output_device} devices={devices.outputs} onChange={(v) => set("output_device", v)} />
            </Field>
            <button type="button" onClick={() => void loadDevices(true)} className="flex items-center gap-1.5 self-end text-[11px] text-ink-dim hover:text-white">
              <RefreshCw size={11} /> Aygıtları yenile
            </button>
          </Section>

          <Section title="Web araştırması">
            <Field label="Araştırma modu" hint="Otomatik: yalnızca güncel bilgi gerektiğinde internette arar.">
              <Segmented
                value={settings.research_mode}
                options={[
                  ["auto", "Otomatik"],
                  ["always", "Her zaman"],
                  ["off", "Kapalı"],
                ]}
                onChange={(v) => set("research_mode", v)}
              />
            </Field>
            <Field label="En fazla kaynak">
              <Slider value={settings.max_sources} min={2} max={6} step={1} format={(v) => String(v)} onChange={(v) => set("max_sources", v)} />
            </Field>
          </Section>

          <Section title="Görsel">
            <Field label="Görsel kalite" hint="Otomatik: kare hızına göre ayarlanır.">
              <Segmented
                value={settings.visual_quality}
                options={[
                  ["auto", "Otomatik"],
                  ["high", "Yüksek"],
                  ["low", "Düşük"],
                ]}
                onChange={(v) => set("visual_quality", v)}
              />
            </Field>
            <Field label="Hareketi azalt" hint="Animasyonları yavaşlatır ve sadeleştirir.">
              <Toggle value={settings.reduced_motion} onChange={(v) => set("reduced_motion", v)} />
            </Field>
          </Section>

          <Section title="Sistem">
            <button
              type="button"
              onClick={() => setUi({ settingsOpen: false, setupOpen: true })}
              className="flex items-center gap-2 self-start rounded-md border border-white/[0.08] px-3 py-1.5 text-[12px] text-ink-dim transition-colors hover:text-white"
            >
              <HardDrive size={13} /> Modeller ve sistem durumu
            </button>
          </Section>
        </div>
      </motion.aside>
    </motion.div>
  );
}
