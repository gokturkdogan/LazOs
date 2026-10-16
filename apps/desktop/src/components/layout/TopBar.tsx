import { History, MessageSquareText, PanelRightClose, Settings2 } from "lucide-react";

import { useApp } from "../../stores/app";
import { stateLabel } from "../ai-core/palette";
import { Emblem } from "../common/Emblem";

type Tone = "ok" | "warn" | "bad" | "off";

const toneColor: Record<Tone, string> = {
  ok: "var(--color-ok)",
  warn: "var(--color-warn)",
  bad: "var(--color-bad)",
  off: "var(--color-ink-faint)",
};

function StatusChip({ label, value, tone, title }: { label: string; value: string; tone: Tone; title?: string }) {
  return (
    <div className="flex items-center gap-2 rounded-md border border-white/5 bg-white/[0.02] px-2.5 py-1" title={title}>
      <span
        className="h-1.5 w-1.5 rounded-full"
        style={{ background: toneColor[tone], boxShadow: tone === "off" ? "none" : `0 0 8px ${toneColor[tone]}` }}
      />
      <span className="hud-label !text-[9px] !tracking-[0.18em]">{label}</span>
      <span className="font-mono text-[10.5px] text-ink">{value}</span>
    </div>
  );
}

function IconButton({ onClick, active, title, children }: { onClick: () => void; active?: boolean; title: string; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      aria-label={title}
      className={`grid h-7 w-7 place-items-center rounded-md border transition-colors ${
        active ? "accent-border bg-white/[0.06] text-white" : "border-transparent text-ink-dim hover:bg-white/[0.05] hover:text-white"
      }`}
    >
      {children}
    </button>
  );
}

export function TopBar() {
  const conn = useApp((s) => s.conn);
  const system = useApp((s) => s.system);
  const aiState = useApp((s) => s.aiState);
  const aiDetail = useApp((s) => s.aiDetail);
  const ui = useApp((s) => s.ui);
  const setUi = useApp((s) => s.setUi);

  const llm = system?.llm;
  const llmTone: Tone = conn !== "online" ? "off" : !llm?.reachable ? "bad" : llm.loaded ? "ok" : "warn";
  const llmValue = !llm?.reachable ? "ÇEVRİMDIŞI" : llm.loaded ? (llm.model ?? "—") : `${llm.model ?? ""} · uykuda`;
  const netTone: Tone = system?.online === null || system?.online === undefined ? "off" : system.online ? "ok" : "bad";
  const searchTone: Tone = !system?.search_provider ? (system?.online ? "warn" : "off") : "ok";

  return (
    <header
      data-tauri-drag-region
      className="relative z-30 flex h-11 shrink-0 items-center gap-4 border-b border-white/[0.05] bg-void/60 pr-3 pl-[84px] backdrop-blur-xl"
    >
      <div data-tauri-drag-region className="flex items-center gap-2.5">
        <Emblem size={18} />
        <span className="font-display text-[15px] font-semibold tracking-[0.42em] text-white">LAZOS</span>
        <span className="font-mono text-[9.5px] text-ink-faint">v{system?.version ?? "1.0.0"}</span>
      </div>

      <div data-tauri-drag-region className="flex flex-1 justify-center">
        <div className="flex items-center gap-2.5 rounded-full border border-white/[0.06] bg-white/[0.02] px-3.5 py-1">
          <span
            className="h-1.5 w-1.5 rounded-full pulse-soft"
            style={{ background: "var(--accent)", boxShadow: "0 0 10px var(--accent)" }}
          />
          <span className="font-display text-[10.5px] font-medium tracking-[0.3em] accent-text">
            {conn === "online" ? stateLabel(aiState, aiDetail) : conn === "error" ? "BAĞLANTI HATASI" : "BAĞLANIYOR"}
          </span>
        </div>
      </div>

      <div className="flex items-center gap-2">
        <StatusChip label="Yerel AI" value={llmValue} tone={llmTone} title="Tüm çıkarım bu Mac üzerinde yapılır" />
        <StatusChip label="Ağ" value={system?.online ? "BAĞLI" : system?.online === false ? "YOK" : "—"} tone={netTone} />
        <StatusChip
          label="Arama"
          value={system?.search_provider ?? "YOK"}
          tone={searchTone}
          title="Web araştırması yalnızca gerektiğinde, arama sorgusuyla dışarı çıkar"
        />
        <div className="mx-1 h-4 w-px bg-white/10" />
        <IconButton title="Geçmiş konuşmalar" active={ui.historyOpen} onClick={() => setUi({ historyOpen: !ui.historyOpen })}>
          <History size={15} />
        </IconButton>
        <IconButton title="Ayarlar" active={ui.settingsOpen} onClick={() => setUi({ settingsOpen: !ui.settingsOpen })}>
          <Settings2 size={15} />
        </IconButton>
        <IconButton title={ui.chatOpen ? "Konuşma panelini gizle" : "Konuşma panelini göster"} onClick={() => setUi({ chatOpen: !ui.chatOpen })}>
          {ui.chatOpen ? <PanelRightClose size={15} /> : <MessageSquareText size={15} />}
        </IconButton>
      </div>
    </header>
  );
}
