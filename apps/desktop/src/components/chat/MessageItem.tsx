import { AlertTriangle, Mic, PenLine } from "lucide-react";
import { motion } from "motion/react";
import { memo, useState } from "react";

import type { ChatMessage } from "../../types/protocol";
import { formatClock } from "../../utils/format";
import { Markdown } from "./Markdown";
import { AnswerFeedback, TranscriptEditor } from "./MessageFeedback";
import { ResearchStatus, SourceList } from "./Sources";

export const MessageItem = memo(function MessageItem({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
  const streaming = message.status === "streaming";
  const [editing, setEditing] = useState(false);

  if (isUser) {
    return (
      <motion.article
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.25 }}
        className="flex flex-col items-end"
      >
        <header className="group/user mb-1 flex items-center gap-1.5">
          {message.origin === "voice" && !editing && (
            <button
              type="button"
              title="Yanlış anlaşıldı mı? Metni düzelt"
              onClick={() => setEditing(true)}
              className="text-ink-faint opacity-0 transition-opacity hover:text-white group-hover/user:opacity-100"
            >
              <PenLine size={10} />
            </button>
          )}
          {message.stt_original && (
            <span className="font-mono text-[9px] text-ink-faint" title={`Whisper: “${message.stt_original}”`}>
              düzeltildi ·
            </span>
          )}
          {message.origin === "voice" && <Mic size={10} className="text-ink-faint" />}
          <span className="hud-label !text-[9px]">Sen</span>
          <span className="font-mono text-[9.5px] text-ink-faint">{formatClock(message.created_at)}</span>
        </header>
        {editing ? (
          <TranscriptEditor message={message} onDone={() => setEditing(false)} />
        ) : (
          <div className="selectable max-w-[88%] whitespace-pre-wrap rounded-lg rounded-tr-sm border border-white/[0.07] bg-white/[0.035] px-3.5 py-2 text-[13.5px] leading-relaxed text-ink">
            {message.content}
          </div>
        )}
      </motion.article>
    );
  }

  const research = message.research;
  return (
    <motion.article
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
      className="group/answer relative pl-4"
    >
      <span
        className="absolute bottom-1 left-0 top-1 w-px"
        style={{
          background: "linear-gradient(to bottom, var(--accent), transparent)",
          boxShadow: streaming ? "0 0 10px var(--accent)" : "none",
        }}
      />
      <header className="mb-1.5 flex items-center gap-2">
        <span className="hud-label !text-[9px] accent-text">Lazos</span>
        <span className="font-mono text-[9.5px] text-ink-faint">{formatClock(message.created_at)}</span>
        {message.metrics?.tokens_per_s && !streaming && (
          <span className="font-mono text-[9px] text-ink-faint/70">
            · {message.metrics.tokens_per_s.toFixed(0)} tok/s · {((message.metrics.total_ms ?? 0) / 1000).toFixed(1)} s
          </span>
        )}
        {message.status === "interrupted" && <span className="font-mono text-[9px] text-warn">· yarıda kesildi</span>}
      </header>

      {research && <ResearchStatus research={research} streaming={streaming} />}

      {message.status === "error" ? (
        <div className="flex items-start gap-2 text-[13px] text-[#ff8a9a]">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          {message.error}
        </div>
      ) : message.content ? (
        <div className={streaming ? "is-streaming" : ""}>
          <Markdown text={message.content} sources={message.sources} />
        </div>
      ) : (
        streaming &&
        research?.status !== "searching" && (
          <div className="flex h-6 items-center gap-1">
            {[0, 1, 2].map((i) => (
              <motion.span
                key={i}
                className="h-1 w-1 rounded-full"
                style={{ background: "var(--accent)" }}
                animate={{ opacity: [0.2, 1, 0.2] }}
                transition={{ duration: 1.1, repeat: Infinity, delay: i * 0.18 }}
              />
            ))}
          </div>
        )
      )}

      {!streaming && <SourceList sources={message.sources} query={research?.query} messageId={message.id} />}
      {!streaming && message.status !== "error" && message.content && <AnswerFeedback message={message} />}
    </motion.article>
  );
});
