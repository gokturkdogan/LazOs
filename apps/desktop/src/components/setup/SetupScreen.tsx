import { CheckCircle2, CircleDashed, Cpu, Download, Globe, Loader2, Mic, Play, Power, Server, XCircle } from "lucide-react";
import { motion } from "motion/react";
import { useEffect, useMemo, useState } from "react";

import { api } from "../../services/api";
import { openExternal } from "../../services/backend";
import { useApp } from "../../stores/app";
import type { ModelInfo } from "../../types/protocol";
import { formatBytes, formatMemory } from "../../utils/format";
import { Emblem } from "../common/Emblem";

type Check = "ok" | "bad" | "pending" | "busy";

function StatusIcon({ status }: { status: Check }) {
  if (status === "ok") return <CheckCircle2 size={15} className="text-ok" />;
  if (status === "bad") return <XCircle size={15} className="text-bad" />;
  if (status === "busy") return <Loader2 size={15} className="animate-spin accent-text" />;
  return <CircleDashed size={15} className="text-ink-faint" />;
}

function Step({ icon, title, status, children }: { icon: React.ReactNode; title: string; status: Check; children: React.ReactNode }) {
  return (
    <div className="flex gap-3.5 border-b border-white/[0.05] py-4 last:border-0">
      <div className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-md border border-white/[0.08] bg-white/[0.02] text-ink-dim">{icon}</div>
      <div className="min-w-0 flex-1">
        <div className="mb-1 flex items-center gap-2">
          <span className="text-[13px] font-medium text-white">{title}</span>
          <StatusIcon status={status} />
        </div>
        <div className="text-[12px] leading-relaxed text-ink-dim">{children}</div>
      </div>
    </div>
  );
}

const modelCheck = (m: ModelInfo): Check =>
  m.status === "present" ? "ok" : m.status === "error" ? "bad" : ["downloading", "converting", "verifying"].includes(m.status) ? "busy" : "pending";

function ModelRow({ m }: { m: ModelInfo }) {
  const busy = ["downloading", "converting", "verifying"].includes(m.status);
  return (
    <div className="rounded-md border border-white/[0.06] bg-white/[0.015] px-3 py-2.5">
      <div className="flex items-center gap-2">
        <StatusIcon status={modelCheck(m)} />
        <span className="flex-1 truncate text-[12.5px] text-ink">{m.label}</span>
        <span className="font-mono text-[10.5px] text-ink-faint">
          {m.status === "present"
            ? "hazır"
            : m.source === "local-convert"
              ? "yerelde bulundu · dönüştürülecek"
              : `≈ ${formatBytes(m.approx_bytes)}`}
        </span>
      </div>
      <div className="mt-1 pl-6 text-[11px] text-ink-faint">{m.description}</div>
      {busy && (
        <div className="mt-2 pl-6">
          <div className="h-1 overflow-hidden rounded-full bg-white/[0.06]">
            <motion.div className="h-full rounded-full" style={{ background: "var(--accent)" }} animate={{ width: `${Math.max(2, m.progress * 100)}%` }} />
          </div>
          <div className="mt-1 flex justify-between font-mono text-[10px] text-ink-faint">
            <span>{m.detail || m.status}</span>
            <span>{(m.progress * 100).toFixed(0)}%</span>
          </div>
        </div>
      )}
      {m.error && <div className="mt-1.5 pl-6 text-[11px] text-bad">{m.error}</div>}
    </div>
  );
}

export function SetupScreen() {
  const setup = useApp((s) => s.setup);
  const models = useApp((s) => s.models);
  const loadSetup = useApp((s) => s.loadSetup);
  const setUi = useApp((s) => s.setUi);
  const pushToast = useApp((s) => s.pushToast);
  const [starting, setStarting] = useState(false);
  const [mic, setMic] = useState<{ state: Check; message?: string }>({ state: "pending" });
  const [installing, setInstalling] = useState(false);

  useEffect(() => {
    void loadSetup();
    const t = window.setInterval(() => void loadSetup(), 4000);
    return () => window.clearInterval(t);
  }, [loadSetup]);

  const list = models ?? setup?.models ?? [];
  const missing = useMemo(() => list.filter((m) => m.status === "missing" || m.status === "error"), [list]);
  const downloadBytes = missing.reduce((sum, m) => sum + m.download_bytes, 0);
  const anyBusy = list.some((m) => ["downloading", "converting", "verifying"].includes(m.status));
  const runtime = setup?.runtime;
  const ready = list.length > 0 && list.every((m) => m.status === "present") && runtime?.ollama_running;
  const sys = setup?.system;

  const startOllama = async () => {
    setStarting(true);
    try {
      const r = await api.startOllama();
      if (!r.running) pushToast("error", r.error ?? "Ollama başlatılamadı.");
      await loadSetup();
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setStarting(false);
    }
  };

  const install = async () => {
    setInstalling(true);
    try {
      await api.install(missing.map((m) => m.id));
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setInstalling(false);
    }
  };

  const testMic = async () => {
    setMic({ state: "busy" });
    try {
      const r = await api.micTest();
      setMic(r.ok ? { state: "ok", message: "Mikrofon çalışıyor." } : { state: "bad", message: r.error });
    } catch (e) {
      setMic({ state: "bad", message: (e as Error).message });
    }
  };

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="absolute inset-0 z-40 flex items-center justify-center bg-void/55 p-6 backdrop-blur-sm"
    >
      <motion.div
        initial={{ y: 16, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        transition={{ duration: 0.45, ease: [0.2, 0.8, 0.2, 1] }}
        className="glass-strong brackets relative flex max-h-full w-[640px] flex-col overflow-hidden rounded-xl"
      >
        <header className="flex items-center gap-3 border-b border-white/[0.06] px-6 py-4">
          <Emblem size={26} />
          <div className="flex-1">
            <div className="font-display text-[15px] font-semibold tracking-[0.35em] text-white">LAZOS KURULUMU</div>
            <div className="text-[11.5px] text-ink-dim">Senin makinen. Senin zekân. Senin kuralların.</div>
          </div>
        </header>

        <div className="scroll-thin flex-1 overflow-y-auto px-6">
          <Step icon={<Cpu size={15} />} title="Sistem uyumluluğu" status={!sys ? "busy" : sys.compatible ? "ok" : "bad"}>
            {sys ? (
              <span className="font-mono text-[11px]">
                {sys.chip || sys.arch} · {formatMemory(sys.memory_bytes)} bellek · {sys.os} · {formatBytes(sys.disk_free_bytes)} boş disk
                {!sys.compatible && <span className="mt-1 block text-bad">LAZOS, en az 16 GB belleğe sahip bir Apple Silicon Mac gerektirir.</span>}
              </span>
            ) : (
              "Kontrol ediliyor…"
            )}
          </Step>

          <Step icon={<Server size={15} />} title="Yerel çıkarım motoru (Ollama)" status={!runtime ? "busy" : runtime.ollama_running ? "ok" : "bad"}>
            {!runtime ? (
              "Kontrol ediliyor…"
            ) : runtime.ollama_running ? (
              <span className="font-mono text-[11px]">Ollama {runtime.ollama_version} çalışıyor · 127.0.0.1:11434</span>
            ) : runtime.ollama_installed ? (
              <div className="flex items-center gap-3">
                <span>Ollama yüklü ama çalışmıyor.</span>
                <button type="button" onClick={() => void startOllama()} disabled={starting} className="flex items-center gap-1.5 rounded-md border accent-border px-2.5 py-1 text-[11.5px] text-white disabled:opacity-50">
                  {starting ? <Loader2 size={12} className="animate-spin" /> : <Power size={12} />} Ollama'yı başlat
                </button>
              </div>
            ) : (
              <span>
                Ollama bulunamadı. Kurulum için{" "}
                <button type="button" className="accent-text underline" onClick={() => void openExternal("https://ollama.com/download/mac")}>
                  ollama.com
                </button>{" "}
                adresini kullan ya da Terminal'de <code className="font-mono text-ink">brew install ollama</code> çalıştır. Sonra bu ekran otomatik güncellenir.
              </span>
            )}
          </Step>

          <Step icon={<Download size={15} />} title="Yapay zekâ modelleri" status={list.length === 0 ? "busy" : missing.length === 0 && !anyBusy ? "ok" : anyBusy ? "busy" : "pending"}>
            <p className="mb-2.5">Tüm modeller bu Mac'te çalışır. Makinende zaten bulunan modeller yeniden indirilmez.</p>
            <div className="flex flex-col gap-1.5">
              {list.map((m) => (
                <ModelRow key={m.id} m={m} />
              ))}
            </div>
            {missing.length > 0 && !anyBusy && (
              <div className="mt-3 flex items-center justify-between gap-3 rounded-md border border-white/[0.07] bg-white/[0.02] px-3 py-2.5">
                <span className="text-[12px]">
                  İndirilecek: <span className="font-mono text-white">{formatBytes(downloadBytes)}</span>
                  {missing.some((m) => m.source === "local-convert") && " (+ yerel dönüştürme)"}
                </span>
                <button
                  type="button"
                  onClick={() => void install()}
                  disabled={installing || (missing.some((m) => m.id === "llm") && !runtime?.ollama_running)}
                  className="flex items-center gap-1.5 rounded-md px-3 py-1.5 text-[12px] font-medium text-void disabled:opacity-40"
                  style={{ background: "var(--accent)" }}
                >
                  {installing ? <Loader2 size={12} className="animate-spin" /> : <Download size={12} />} Onayla ve yükle
                </button>
              </div>
            )}
          </Step>

          <Step icon={<Mic size={15} />} title="Mikrofon" status={mic.state}>
            <div className="flex items-center gap-3">
              <span className="flex-1">
                {mic.message ?? "Sesli konuşma için mikrofon izni gerekir. Test, macOS izin penceresini açabilir."}
              </span>
              <button type="button" onClick={() => void testMic()} disabled={mic.state === "busy"} className="shrink-0 rounded-md border border-white/10 px-2.5 py-1 text-[11.5px] text-ink hover:text-white disabled:opacity-50">
                Mikrofonu test et
              </button>
            </div>
          </Step>

          <Step icon={<Globe size={15} />} title="Web araştırması" status={!setup ? "busy" : setup.search.some((p) => p.available) ? "ok" : "pending"}>
            {setup?.search.map((p) => (
              <span key={p.name} className="mr-3 font-mono text-[11px]">
                {p.name}: {p.available ? "hazır" : "kullanılamıyor"}
              </span>
            ))}
            <span className="mt-1 block">
              İnternete yalnızca güncel bilgi gerektiğinde arama sorgusu gönderilir; konuşmaların asla dışarı yüklenmez.
            </span>
          </Step>
        </div>

        <footer className="flex items-center justify-between gap-3 border-t border-white/[0.06] px-6 py-4">
          <span className="text-[11px] text-ink-faint">{ready ? "Her şey hazır." : "Gerekli adımlar tamamlanınca LAZOS başlatılabilir."}</span>
          <button
            type="button"
            disabled={!ready}
            onClick={() => setUi({ setupOpen: false })}
            className="flex items-center gap-2 rounded-md px-4 py-2 font-display text-[11px] font-semibold tracking-[0.25em] text-void transition-opacity disabled:opacity-30"
            style={{ background: "var(--accent)", boxShadow: ready ? "0 0 28px color-mix(in srgb, var(--accent) 50%, transparent)" : "none" }}
          >
            <Play size={12} fill="currentColor" /> LAZOS'U BAŞLAT
          </button>
        </footer>
      </motion.div>
    </motion.div>
  );
}
