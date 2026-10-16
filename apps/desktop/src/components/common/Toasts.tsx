import { AlertTriangle, Info, X } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect } from "react";

import { useApp } from "../../stores/app";
import type { Toast } from "../../stores/reducer";

function ToastItem({ toast }: { toast: Toast }) {
  const dismiss = useApp((s) => s.dismissToast);
  useEffect(() => {
    const t = window.setTimeout(() => dismiss(toast.id), toast.level === "error" ? 7000 : 4000);
    return () => window.clearTimeout(t);
  }, [toast, dismiss]);
  const error = toast.level === "error";
  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: -10, scale: 0.97 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: -6, scale: 0.97 }}
      className="glass-strong pointer-events-auto flex max-w-md items-start gap-2.5 rounded-lg px-3.5 py-2.5"
      style={{ borderColor: error ? "rgb(255 23 68 / 0.35)" : undefined }}
      role={error ? "alert" : "status"}
    >
      {error ? <AlertTriangle size={14} className="mt-0.5 shrink-0 text-bad" /> : <Info size={14} className="mt-0.5 shrink-0 accent-text" />}
      <p className="selectable flex-1 text-[12.5px] leading-snug text-ink">{toast.message}</p>
      <button type="button" onClick={() => dismiss(toast.id)} className="text-ink-faint hover:text-white" aria-label="Kapat">
        <X size={12} />
      </button>
    </motion.div>
  );
}

export function Toasts() {
  const toasts = useApp((s) => s.toasts);
  return (
    <div className="pointer-events-none absolute inset-x-0 top-14 z-50 flex flex-col items-center gap-2">
      <AnimatePresence>
        {toasts.map((t) => (
          <ToastItem key={t.id} toast={t} />
        ))}
      </AnimatePresence>
    </div>
  );
}
