import { AlertTriangle, Loader2, RotateCw } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";

import { restartBackend } from "../../services/backend";
import { useApp } from "../../stores/app";

/** Shown while the local backend is starting, or when it cannot be reached. */
export function ConnectionOverlay() {
  const conn = useApp((s) => s.conn);
  const error = useApp((s) => s.connError);
  const visible = conn !== "online";
  const failed = conn === "error";

  return (
    <AnimatePresence>
      {visible && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.4 }}
          className="pointer-events-none absolute inset-x-0 top-1/2 z-30 mt-[270px] flex justify-center"
        >
          <div className="glass pointer-events-auto flex max-w-lg items-center gap-3 rounded-lg px-4 py-2.5">
            {failed ? <AlertTriangle size={14} className="shrink-0 text-bad" /> : <Loader2 size={14} className="shrink-0 animate-spin accent-text" />}
            <div className="text-[12.5px] text-ink">
              {failed
                ? (error ?? "Yerel servis başlatılamadı.")
                : conn === "offline"
                  ? "Yerel servisle bağlantı koptu — yeniden bağlanılıyor…"
                  : (error ?? "Yerel yapay zekâ servisi başlatılıyor…")}
            </div>
            {failed && (
              <button
                type="button"
                onClick={() => {
                  void restartBackend();
                  window.setTimeout(() => window.location.reload(), 400);
                }}
                className="flex shrink-0 items-center gap-1.5 rounded-md border border-white/10 px-2.5 py-1 text-[11px] text-ink-dim hover:text-white"
              >
                <RotateCw size={11} /> Yeniden dene
              </button>
            )}
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
