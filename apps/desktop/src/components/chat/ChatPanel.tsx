import { Plus, Sparkles } from "lucide-react";
import { motion } from "motion/react";
import { useEffect, useLayoutEffect, useRef } from "react";

import { useApp } from "../../stores/app";
import { MessageItem } from "./MessageItem";

const SUGGESTIONS = [
  "Bugün yapay zekâ dünyasında neler oldu?",
  "Bana kuantum bilgisayarları kısaca anlat.",
  "Python'da bir listeyi nasıl ters çeviririm?",
  "Dolar kuru şu an ne kadar?",
];

function EmptyState() {
  const send = useApp((s) => s.sendMessage);
  return (
    <div className="flex h-full flex-col items-center justify-center px-6 text-center">
      <Sparkles size={18} className="mb-3 accent-text" />
      <p className="font-display text-[13px] tracking-[0.25em] text-white">YENİ KONUŞMA</p>
      <p className="mt-2 max-w-[260px] text-[12.5px] leading-relaxed text-ink-dim">
        Yaz ya da mikrofona dokunarak konuş. Tüm yanıtlar bu Mac üzerinde üretilir.
      </p>
      <div className="mt-6 flex w-full flex-col gap-1.5">
        {SUGGESTIONS.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => send(s)}
            className="rounded-md border border-white/[0.06] bg-white/[0.02] px-3 py-2 text-left text-[12px] text-ink-dim transition-colors hover:border-white/[0.12] hover:bg-white/[0.05] hover:text-white"
          >
            {s}
          </button>
        ))}
      </div>
    </div>
  );
}

export function ChatPanel() {
  const messages = useApp((s) => s.messages);
  const conversationId = useApp((s) => s.conversationId);
  const title = useApp((s) => s.conversations.find((c) => c.id === s.conversationId)?.title);
  const newConversation = useApp((s) => s.newConversation);
  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const onScroll = () => {
      stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

  useLayoutEffect(() => {
    const el = scroller.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [messages]);

  useEffect(() => {
    stick.current = true;
  }, [conversationId]);

  return (
    <motion.aside
      initial={{ opacity: 0, x: 24 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: 24 }}
      transition={{ duration: 0.35, ease: [0.2, 0.8, 0.2, 1] }}
      className="glass relative z-20 m-3 ml-0 flex w-[420px] shrink-0 flex-col overflow-hidden rounded-xl"
    >
      <header className="flex h-12 shrink-0 items-center gap-3 border-b border-white/[0.06] px-4">
        <div className="min-w-0 flex-1">
          <div className="hud-label !text-[9px] accent-text">Konuşma</div>
          <div className="truncate text-[12.5px] text-ink">{title ?? "Yeni konuşma"}</div>
        </div>
        <button
          type="button"
          onClick={newConversation}
          title="Yeni konuşma"
          className="grid h-7 w-7 place-items-center rounded-md text-ink-dim transition-colors hover:bg-white/[0.06] hover:text-white"
        >
          <Plus size={15} />
        </button>
      </header>
      <div ref={scroller} className="scroll-thin flex-1 overflow-y-auto px-4 py-4">
        {messages.length === 0 ? (
          <EmptyState />
        ) : (
          <div className="flex flex-col gap-5">
            {messages.map((m) => (
              <MessageItem key={m.id} message={m} />
            ))}
          </div>
        )}
      </div>
    </motion.aside>
  );
}
