import { Check, MessageSquare, Pencil, Plus, Search, Trash2, X } from "lucide-react";
import { motion } from "motion/react";
import { useMemo, useState } from "react";

import { useApp } from "../../stores/app";
import type { ConversationSummary } from "../../types/protocol";
import { formatClock, formatRelativeDay } from "../../utils/format";

function Row({ conv, active }: { conv: ConversationSummary; active: boolean }) {
  const open = useApp((s) => s.openConversation);
  const remove = useApp((s) => s.deleteConversation);
  const rename = useApp((s) => s.renameConversation);
  const setUi = useApp((s) => s.setUi);
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(conv.title);
  const [confirm, setConfirm] = useState(false);

  if (editing) {
    return (
      <form
        className="flex items-center gap-1.5 rounded-md border accent-border bg-white/[0.04] px-2 py-1.5"
        onSubmit={(e) => {
          e.preventDefault();
          if (title.trim()) void rename(conv.id, title.trim());
          setEditing(false);
        }}
      >
        <input
          autoFocus
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          onKeyDown={(e) => e.key === "Escape" && setEditing(false)}
          className="selectable min-w-0 flex-1 bg-transparent text-[12.5px] text-white outline-none"
        />
        <button type="submit" className="text-ink-dim hover:text-white" aria-label="Kaydet">
          <Check size={13} />
        </button>
      </form>
    );
  }

  return (
    <div
      className={`group flex items-center gap-2 rounded-md border px-2.5 py-2 transition-colors ${
        active ? "accent-border bg-white/[0.05]" : "border-transparent hover:bg-white/[0.035]"
      }`}
    >
      <button
        type="button"
        className="flex min-w-0 flex-1 items-center gap-2.5 text-left"
        onClick={() => {
          void open(conv.id);
          setUi({ historyOpen: false, chatOpen: true });
        }}
      >
        <MessageSquare size={13} className={active ? "accent-text shrink-0" : "shrink-0 text-ink-faint"} />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[12.5px] text-ink">{conv.title}</span>
          <span className="font-mono text-[9.5px] text-ink-faint">
            {formatClock(conv.updated_at)} · {conv.message_count} mesaj
          </span>
        </span>
      </button>
      {confirm ? (
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => void remove(conv.id)}
            className="rounded px-1.5 py-0.5 font-mono text-[9.5px] text-bad hover:bg-bad/10"
          >
            SİL
          </button>
          <button type="button" onClick={() => setConfirm(false)} className="text-ink-faint hover:text-white" aria-label="Vazgeç">
            <X size={12} />
          </button>
        </div>
      ) : (
        <div className="flex items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
          <button type="button" title="Yeniden adlandır" onClick={() => setEditing(true)} className="grid h-6 w-6 place-items-center rounded text-ink-faint hover:bg-white/10 hover:text-white">
            <Pencil size={11} />
          </button>
          <button type="button" title="Sil" onClick={() => setConfirm(true)} className="grid h-6 w-6 place-items-center rounded text-ink-faint hover:bg-bad/15 hover:text-bad">
            <Trash2 size={11} />
          </button>
        </div>
      )}
    </div>
  );
}

export function HistoryDrawer() {
  const conversations = useApp((s) => s.conversations);
  const activeId = useApp((s) => s.conversationId);
  const setUi = useApp((s) => s.setUi);
  const newConversation = useApp((s) => s.newConversation);
  const [query, setQuery] = useState("");

  const groups = useMemo(() => {
    const filtered = conversations.filter((c) => c.title.toLocaleLowerCase("tr").includes(query.toLocaleLowerCase("tr")));
    const map = new Map<string, ConversationSummary[]>();
    for (const c of filtered) {
      const key = formatRelativeDay(c.updated_at);
      map.set(key, [...(map.get(key) ?? []), c]);
    }
    return [...map.entries()];
  }, [conversations, query]);

  return (
    <motion.aside
      initial={{ opacity: 0, x: -24 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: -24 }}
      transition={{ duration: 0.3, ease: [0.2, 0.8, 0.2, 1] }}
      className="glass-strong absolute bottom-3 left-3 top-3 z-30 flex w-[320px] flex-col overflow-hidden rounded-xl"
    >
      <header className="flex h-12 shrink-0 items-center gap-2 border-b border-white/[0.06] px-4">
        <span className="hud-label flex-1 accent-text">Konuşma geçmişi</span>
        <button
          type="button"
          onClick={() => {
            newConversation();
            setUi({ historyOpen: false, chatOpen: true });
          }}
          title="Yeni konuşma"
          className="grid h-7 w-7 place-items-center rounded-md text-ink-dim hover:bg-white/[0.06] hover:text-white"
        >
          <Plus size={14} />
        </button>
        <button type="button" onClick={() => setUi({ historyOpen: false })} title="Kapat" className="grid h-7 w-7 place-items-center rounded-md text-ink-dim hover:bg-white/[0.06] hover:text-white">
          <X size={14} />
        </button>
      </header>
      <div className="px-3 pt-3">
        <label className="flex items-center gap-2 rounded-md border border-white/[0.07] bg-white/[0.02] px-2.5 py-1.5">
          <Search size={12} className="text-ink-faint" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Ara…"
            className="selectable w-full bg-transparent text-[12px] text-ink outline-none placeholder:text-ink-faint"
          />
        </label>
      </div>
      <div className="scroll-thin flex-1 overflow-y-auto px-3 py-3">
        {groups.length === 0 && (
          <p className="px-2 py-8 text-center text-[12px] text-ink-faint">{query ? "Eşleşen konuşma yok." : "Henüz kayıtlı konuşma yok."}</p>
        )}
        {groups.map(([day, list]) => (
          <section key={day} className="mb-4">
            <div className="hud-label mb-1.5 px-1 !text-[9px]">{day}</div>
            <div className="flex flex-col gap-0.5">
              {list.map((c) => (
                <Row key={c.id} conv={c} active={c.id === activeId} />
              ))}
            </div>
          </section>
        ))}
      </div>
      <footer className="border-t border-white/[0.06] px-4 py-2.5 font-mono text-[9.5px] text-ink-faint">
        Tüm konuşmalar yalnızca bu Mac'te saklanır.
      </footer>
    </motion.aside>
  );
}
