import {
  Brain,
  Check,
  Fingerprint,
  GraduationCap,
  History,
  Loader2,
  Lock,
  MessagesSquare,
  Pencil,
  Plus,
  RotateCcw,
  Search,
  SlidersHorizontal,
  Trash2,
  UserRound,
  X,
} from "lucide-react";
import { motion } from "motion/react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { api } from "../../services/api";
import { useApp } from "../../stores/app";
import type { CoreIdentity, EpisodeHit, MemoryCategory, MemoryItem, MemoryOverview } from "../../types/protocol";
import { formatRelativeDay } from "../../utils/format";
import { LearningTab } from "./LearningTab";

type Tab = "identity" | "creator" | "learned" | "learning" | "episodes" | "settings";

const TABS: { id: Tab; label: string; icon: React.ReactNode }[] = [
  { id: "identity", label: "Çekirdek Kimlik", icon: <Fingerprint size={14} /> },
  { id: "creator", label: "Yaratıcı Profili", icon: <UserRound size={14} /> },
  { id: "learned", label: "Öğrenilenler", icon: <Brain size={14} /> },
  { id: "learning", label: "Öğrenme", icon: <GraduationCap size={14} /> },
  { id: "episodes", label: "Konuşma Hafızası", icon: <MessagesSquare size={14} /> },
  { id: "settings", label: "Hafıza Ayarları", icon: <SlidersHorizontal size={14} /> },
];

const ORIGIN_LABEL: Record<MemoryItem["origin"], string> = {
  auto: "otomatik öğrenildi",
  explicit: "isteğinle kaydedildi",
  manual: "elle eklendi",
  feedback: "geri bildirimden onaylandı",
};

const inputCls =
  "selectable w-full rounded-md border border-white/[0.08] bg-abyss px-2.5 py-1.5 text-[12.5px] text-ink outline-none focus:border-[color-mix(in_srgb,var(--accent)_45%,transparent)]";

/** A button that needs a second click to confirm (native dialogs are not available in the app window). */
function ConfirmButton({
  onConfirm,
  label,
  confirmLabel,
  icon,
  danger,
  disabled,
}: {
  onConfirm: () => void | Promise<void>;
  label: string;
  confirmLabel: string;
  icon: React.ReactNode;
  danger?: boolean;
  disabled?: boolean;
}) {
  const [armed, setArmed] = useState(false);
  useEffect(() => {
    if (!armed) return;
    const t = window.setTimeout(() => setArmed(false), 4000);
    return () => window.clearTimeout(t);
  }, [armed]);
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={() => {
        if (armed) {
          setArmed(false);
          void onConfirm();
        } else setArmed(true);
      }}
      className={`flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-[11.5px] transition-colors disabled:opacity-40 ${
        armed
          ? danger
            ? "border-bad/60 bg-bad/15 text-white"
            : "accent-border bg-white/[0.08] text-white"
          : "border-white/[0.08] text-ink-dim hover:text-white"
      }`}
    >
      {icon}
      {armed ? confirmLabel : label}
    </button>
  );
}

// ── Core identity & creator profile ───────────────────────────────────────
const IDENTITY_FIELDS: Record<"identity" | "creator", { key: keyof CoreIdentity; label: string; multiline?: boolean }[]> = {
  identity: [
    { key: "name", label: "Ad" },
    { key: "origin", label: "Köken" },
    { key: "purpose", label: "Amaç", multiline: true },
    { key: "identity_description", label: "Kimlik tanımı", multiline: true },
  ],
  creator: [
    { key: "creator_name", label: "Yaratıcı" },
    { key: "creator_description", label: "Yaratıcı tanımı", multiline: true },
  ],
};

function IdentityEditor({ overview, section, onSaved }: { overview: MemoryOverview; section: "identity" | "creator"; onSaved: () => void }) {
  const pushToast = useApp((s) => s.pushToast);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<CoreIdentity>(overview.identity);
  useEffect(() => setDraft(overview.identity), [overview.identity]);

  const save = async () => {
    try {
      await api.saveIdentity(draft);
      setEditing(false);
      pushToast("info", "Çekirdek kimlik güncellendi.");
      onSaved();
    } catch (e) {
      pushToast("error", `Kaydedilemedi: ${(e as Error).message}`);
    }
  };

  return (
    <div>
      <p className="mb-4 text-[12px] leading-relaxed text-ink-dim">
        {section === "identity"
          ? "LAZOS'un kim olduğu. Her konuşmaya otomatik yüklenir ve yalnızca buradan değiştirilebilir; sohbet sırasında değişmez."
          : "LAZOS'u kimin geliştirdiği. LAZOS aksi belirtilmedikçe seninle konuştuğunu varsayar."}
      </p>
      <dl className="flex flex-col gap-3.5">
        {IDENTITY_FIELDS[section].map((f) => (
          <div key={f.key}>
            <dt className="hud-label mb-1 !text-[9px]">{f.label}</dt>
            <dd>
              {editing ? (
                f.multiline ? (
                  <textarea
                    value={draft[f.key]}
                    rows={f.key === "identity_description" ? 4 : 2}
                    onChange={(e) => setDraft({ ...draft, [f.key]: e.target.value })}
                    className={`${inputCls} resize-none leading-relaxed`}
                  />
                ) : (
                  <input value={draft[f.key]} onChange={(e) => setDraft({ ...draft, [f.key]: e.target.value })} className={inputCls} />
                )
              ) : (
                <div className="selectable text-[13px] leading-relaxed text-ink">{overview.identity[f.key]}</div>
              )}
            </dd>
          </div>
        ))}
      </dl>
      <div className="mt-5 flex items-center gap-2">
        {editing ? (
          <>
            <ConfirmButton onConfirm={save} label="Kaydet" confirmLabel="Kimliği değiştir — onayla" icon={<Check size={12} />} />
            <button
              type="button"
              onClick={() => {
                setDraft(overview.identity);
                setEditing(false);
              }}
              className="rounded-md px-2.5 py-1 text-[11.5px] text-ink-dim hover:text-white"
            >
              Vazgeç
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              onClick={() => setEditing(true)}
              className="flex items-center gap-1.5 rounded-md border border-white/[0.08] px-2.5 py-1 text-[11.5px] text-ink-dim hover:text-white"
            >
              <Pencil size={12} /> Düzenle
            </button>
            <ConfirmButton
              onConfirm={async () => {
                await api.resetIdentity();
                onSaved();
              }}
              label="Varsayılana dön"
              confirmLabel="Varsayılana dön — onayla"
              icon={<RotateCcw size={12} />}
            />
          </>
        )}
      </div>
      {section === "creator" && (
        <div className="mt-7">
          <div className="hud-label mb-2 !text-[9px] accent-text">LAZOS'un öğrendiği kimlik bilgileri</div>
          <MemoryList items={overview.memories.filter((m) => m.category === "user_identity")} categories={overview.categories} onChanged={onSaved} />
        </div>
      )}
    </div>
  );
}

// ── Learned memories ──────────────────────────────────────────────────────
function MemoryRow({ item, categories, onChanged }: { item: MemoryItem; categories: MemoryOverview["categories"]; onChanged: () => void }) {
  const pushToast = useApp((s) => s.pushToast);
  const openConversation = useApp((s) => s.openConversation);
  const setUi = useApp((s) => s.setUi);
  const [editing, setEditing] = useState(false);
  const [content, setContent] = useState(item.content);
  const [category, setCategory] = useState<MemoryCategory>(item.category);
  const [showHistory, setShowHistory] = useState(false);

  const save = async () => {
    try {
      await api.updateMemory(item.id, { content, category });
      setEditing(false);
      onChanged();
    } catch (e) {
      pushToast("error", (e as Error).message);
    }
  };

  return (
    <div className="group rounded-md border border-white/[0.06] bg-white/[0.015] px-3 py-2.5">
      {editing ? (
        <div className="flex flex-col gap-2">
          <textarea value={content} rows={2} onChange={(e) => setContent(e.target.value)} className={`${inputCls} resize-none`} />
          <div className="flex items-center gap-2">
            <select value={category} onChange={(e) => setCategory(e.target.value as MemoryCategory)} className={`${inputCls} !w-auto`}>
              {Object.entries(categories).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
            <button type="button" onClick={() => void save()} className="flex items-center gap-1 rounded-md border accent-border px-2.5 py-1 text-[11.5px] text-white">
              <Check size={12} /> Kaydet
            </button>
            <button type="button" onClick={() => setEditing(false)} className="px-2 text-[11.5px] text-ink-dim hover:text-white">
              Vazgeç
            </button>
          </div>
        </div>
      ) : (
        <div className="flex items-start gap-2.5">
          <div className="min-w-0 flex-1">
            <p className="selectable text-[13px] leading-snug text-ink">{item.content}</p>
            <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 font-mono text-[9.5px] text-ink-faint">
              <span className="rounded border border-white/[0.08] px-1.5 py-[1px] text-ink-dim">{item.category_label}</span>
              <span>{ORIGIN_LABEL[item.origin]}</span>
              <span>· {formatRelativeDay(item.updated_at)}</span>
              {item.sensitive && (
                <span className="flex items-center gap-1 text-warn">
                  <Lock size={9} /> hassas
                </span>
              )}
              {item.source_conversation_id && (
                <button
                  type="button"
                  onClick={() => {
                    void openConversation(item.source_conversation_id!);
                    setUi({ memoryOpen: false, chatOpen: true });
                  }}
                  className="underline decoration-white/20 underline-offset-2 hover:text-white"
                >
                  kaynak konuşma
                </button>
              )}
              {item.history.length > 0 && (
                <button type="button" onClick={() => setShowHistory(!showHistory)} className="flex items-center gap-1 hover:text-white">
                  <History size={9} /> {item.history.length} önceki sürüm
                </button>
              )}
            </div>
            {showHistory && (
              <ul className="mt-2 border-l border-white/10 pl-2.5 text-[11.5px] text-ink-faint">
                {item.history.map((h) => (
                  <li key={h.until} className="line-through decoration-white/20">
                    {h.content}
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div className="flex shrink-0 items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100">
            <button type="button" title="Düzenle" onClick={() => setEditing(true)} className="grid h-6 w-6 place-items-center rounded text-ink-faint hover:bg-white/10 hover:text-white">
              <Pencil size={11} />
            </button>
            <ConfirmButton
              onConfirm={async () => {
                await api.deleteMemory(item.id);
                onChanged();
              }}
              label=""
              confirmLabel="Sil"
              danger
              icon={<Trash2 size={11} />}
            />
          </div>
        </div>
      )}
    </div>
  );
}

function MemoryList({ items, categories, onChanged }: { items: MemoryItem[]; categories: MemoryOverview["categories"]; onChanged: () => void }) {
  if (!items.length) return <p className="py-6 text-center text-[12px] text-ink-faint">Henüz kayıt yok.</p>;
  return (
    <div className="flex flex-col gap-1.5">
      {items.map((m) => (
        <MemoryRow key={`${m.id}-${m.updated_at}`} item={m} categories={categories} onChanged={onChanged} />
      ))}
    </div>
  );
}

function LearnedTab({ overview, onChanged }: { overview: MemoryOverview; onChanged: () => void }) {
  const pushToast = useApp((s) => s.pushToast);
  const [filter, setFilter] = useState<MemoryCategory | "all">("all");
  const [category, setCategory] = useState<MemoryCategory>("preference");
  const [content, setContent] = useState("");
  const items = useMemo(
    () => overview.memories.filter((m) => filter === "all" || m.category === filter),
    [overview.memories, filter],
  );

  const add = async () => {
    if (content.trim().length < 3) return;
    try {
      const created = await api.addMemory(category, content.trim());
      if (!created.created) pushToast("info", "Benzer bir kayıt zaten vardı; o güncellendi.");
      setContent("");
      onChanged();
    } catch (e) {
      pushToast("error", (e as Error).message);
    }
  };

  return (
    <div>
      <p className="mb-3 text-[12px] leading-relaxed text-ink-dim">
        LAZOS'un konuşmalardan öğrendikleri. Kimlik, iletişim ve kişisel tercihler her konuşmaya yüklenir; diğerleri yalnızca ilgili
        olduğunda hatırlanır.
      </p>
      <form
        className="mb-4 flex items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void add();
        }}
      >
        <select value={category} onChange={(e) => setCategory(e.target.value as MemoryCategory)} className={`${inputCls} !w-40`}>
          {Object.entries(overview.categories).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </select>
        <input value={content} onChange={(e) => setContent(e.target.value)} placeholder="Kullanıcı …" className={inputCls} />
        <button
          type="submit"
          disabled={content.trim().length < 3}
          className="flex shrink-0 items-center gap-1 rounded-md px-3 py-1.5 text-[12px] font-medium text-void disabled:opacity-30"
          style={{ background: "var(--accent)" }}
        >
          <Plus size={12} /> Ekle
        </button>
      </form>
      <div className="mb-3 flex flex-wrap gap-1.5">
        {(["all", ...Object.keys(overview.categories)] as (MemoryCategory | "all")[]).map((k) => {
          const count = k === "all" ? overview.memories.length : (overview.stats.by_category[k] ?? 0);
          return (
            <button
              key={k}
              type="button"
              onClick={() => setFilter(k)}
              className={`rounded-full border px-2.5 py-0.5 text-[11px] transition-colors ${
                filter === k ? "accent-border bg-white/[0.08] text-white" : "border-white/[0.07] text-ink-dim hover:text-white"
              }`}
            >
              {k === "all" ? "Tümü" : overview.categories[k]} <span className="font-mono text-ink-faint">{count}</span>
            </button>
          );
        })}
      </div>
      <MemoryList items={items} categories={overview.categories} onChanged={onChanged} />
      {overview.memories.length > 0 && (
        <div className="mt-5 flex justify-end">
          <ConfirmButton
            onConfirm={async () => {
              const r = await api.clearMemories();
              pushToast("info", `${r.deleted} öğrenilen kayıt silindi. Çekirdek kimlik korunuyor.`);
              onChanged();
            }}
            label="Tüm öğrenilenleri temizle"
            confirmLabel="Hepsini kalıcı olarak sil — onayla"
            danger
            icon={<Trash2 size={12} />}
          />
        </div>
      )}
    </div>
  );
}

// ── Conversation (episodic) memory ────────────────────────────────────────
function EpisodesTab({ overview }: { overview: MemoryOverview }) {
  const openConversation = useApp((s) => s.openConversation);
  const setUi = useApp((s) => s.setUi);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<EpisodeHit[] | null>(null);
  const [busy, setBusy] = useState(false);

  const search = async () => {
    if (!query.trim()) return;
    setBusy(true);
    try {
      setHits((await api.searchEpisodes(query.trim())).hits);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <p className="mb-3 text-[12px] leading-relaxed text-ink-dim">
        LAZOS, “geçen hafta ne konuşmuştuk?” gibi sorularda geçmiş konuşmalarını yerel olarak arar ve yalnızca gerçekten kayıtlı olanı
        söyler. <span className="font-mono text-ink-faint">{overview.stats.conversations} konuşma · {overview.stats.indexed_messages} mesaj dizinlendi</span>
      </p>
      <form
        className="mb-4 flex items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void search();
        }}
      >
        <label className="flex flex-1 items-center gap-2 rounded-md border border-white/[0.08] bg-abyss px-2.5">
          <Search size={12} className="text-ink-faint" />
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Geçmiş konuşmalarda ara…" className="selectable w-full bg-transparent py-1.5 text-[12.5px] text-ink outline-none" />
        </label>
        <button type="submit" className="flex items-center gap-1 rounded-md border border-white/[0.1] px-3 py-1.5 text-[12px] text-ink hover:text-white">
          {busy ? <Loader2 size={12} className="animate-spin" /> : <Search size={12} />} Ara
        </button>
      </form>
      {hits && hits.length === 0 && <p className="py-6 text-center text-[12px] text-ink-faint">Eşleşen konuşma bulunamadı.</p>}
      <div className="flex flex-col gap-1.5">
        {hits?.map((h) => (
          <button
            key={h.message_id}
            type="button"
            onClick={() => {
              void openConversation(h.conversation_id);
              setUi({ memoryOpen: false, chatOpen: true });
            }}
            className="rounded-md border border-white/[0.06] bg-white/[0.015] px-3 py-2 text-left transition-colors hover:bg-white/[0.04]"
          >
            <div className="mb-1 flex items-center gap-2 font-mono text-[9.5px] text-ink-faint">
              <span className="accent-text">{h.role === "user" ? "SEN" : "LAZOS"}</span>
              <span>· {formatRelativeDay(h.created_at)}</span>
              <span className="truncate">· {h.conversation_title}</span>
            </div>
            <p className="line-clamp-3 text-[12.5px] leading-snug text-ink">{h.content}</p>
          </button>
        ))}
      </div>
    </div>
  );
}

// ── Settings ──────────────────────────────────────────────────────────────
function Toggle({ value, onChange }: { value: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={value}
      onClick={() => onChange(!value)}
      className="relative h-5 w-9 shrink-0 rounded-full transition-colors"
      style={{ background: value ? "var(--accent)" : "rgb(255 255 255 / 0.12)" }}
    >
      <span className="absolute top-0.5 h-4 w-4 rounded-full bg-white transition-all" style={{ left: value ? 18 : 2 }} />
    </button>
  );
}

function SettingsTab({ overview }: { overview: MemoryOverview }) {
  const settings = useApp((s) => s.settings);
  const update = useApp((s) => s.updateSettings);
  if (!settings) return null;
  const Row = ({ title, hint, value, onChange }: { title: string; hint: string; value: boolean; onChange: (v: boolean) => void }) => (
    <div className="flex items-start justify-between gap-4 border-b border-white/[0.05] py-3.5">
      <div>
        <div className="text-[12.5px] text-ink">{title}</div>
        <div className="mt-0.5 text-[11px] leading-snug text-ink-faint">{hint}</div>
      </div>
      <Toggle value={value} onChange={onChange} />
    </div>
  );
  return (
    <div>
      <Row
        title="Otomatik öğrenme"
        hint="LAZOS, yanıt verdikten sonra arka planda konuşmadaki kalıcı bilgileri (tercihler, projeler, ilgi alanları) çıkarır. Kapalıyken yalnızca “bunu hatırla” dediklerin kaydedilir."
        value={settings.memory_auto_learn}
        onChange={(v) => void update({ memory_auto_learn: v })}
      />
      <Row
        title="Hafızayı yanıtlarda kullan"
        hint="Öğrenilenleri ve geçmiş konuşmaları yanıtlarda kullanır. Kapalıyken LAZOS yalnızca çekirdek kimliğini bilir."
        value={settings.memory_enabled}
        onChange={(v) => void update({ memory_enabled: v })}
      />
      <div className="mt-5 rounded-md border border-white/[0.06] bg-white/[0.015] px-3.5 py-3 text-[11.5px] leading-relaxed text-ink-dim">
        <div className="hud-label mb-1.5 !text-[9px] accent-text">Gizlilik kuralları</div>
        <ul className="list-disc space-y-1 pl-4">
          <li>Hafıza yalnızca bu Mac'teki yerel veritabanında tutulur; hiçbir yere gönderilmez.</li>
          <li>Sağlık, inanç, siyasi görüş, finans gibi hassas bilgiler yalnızca açıkça “hatırla” dersen kaydedilir.</li>
          <li>Şifre, kart numarası, IBAN gibi gizli bilgiler hiçbir koşulda kaydedilmez.</li>
          <li>Varsayımsal cümleler (“diyelim ki…”) ve sorular kalıcı bilgi sayılmaz.</li>
          <li>“… unut” dediğinde ilgili kayıt silinir; silme işlemi yalnızca açık istekle yapılır.</li>
        </ul>
        {overview.pending_learning > 0 && <p className="mt-2 font-mono text-[10px]">{overview.pending_learning} mesaj öğrenme kuyruğunda.</p>}
      </div>
    </div>
  );
}

// ── Panel ─────────────────────────────────────────────────────────────────
export function MemoryPanel() {
  const setUi = useApp((s) => s.setUi);
  const revision = useApp((s) => s.memoryRevision);
  const pushToast = useApp((s) => s.pushToast);
  const [tab, setTab] = useState<Tab>("learned");
  const [overview, setOverview] = useState<MemoryOverview | null>(null);

  const load = useCallback(() => {
    api
      .memory()
      .then(setOverview)
      .catch((e) => pushToast("error", `Hafıza yüklenemedi: ${(e as Error).message}`));
  }, [pushToast]);

  useEffect(() => {
    load();
  }, [load, revision]);

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="absolute inset-0 z-40 flex items-center justify-center bg-void/55 p-6 backdrop-blur-sm"
      onClick={() => setUi({ memoryOpen: false })}
    >
      <motion.div
        initial={{ y: 14, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        exit={{ y: 10, opacity: 0 }}
        transition={{ duration: 0.35, ease: [0.2, 0.8, 0.2, 1] }}
        onClick={(e) => e.stopPropagation()}
        className="glass-strong brackets relative flex h-[min(760px,100%)] w-[900px] max-w-full overflow-hidden rounded-xl"
      >
        <nav className="flex w-52 shrink-0 flex-col border-r border-white/[0.06] p-3">
          <div className="mb-4 flex items-center gap-2 px-2 pt-1">
            <Brain size={16} className="accent-text" />
            <span className="font-display text-[12px] font-semibold tracking-[0.3em] text-white">HAFIZA</span>
          </div>
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => setTab(t.id)}
              className={`mb-0.5 flex items-center gap-2.5 rounded-md px-2.5 py-2 text-left text-[12.5px] transition-colors ${
                tab === t.id ? "bg-white/[0.07] text-white" : "text-ink-dim hover:bg-white/[0.03] hover:text-white"
              }`}
            >
              <span className={tab === t.id ? "accent-text" : ""}>{t.icon}</span>
              {t.label}
              {t.id === "learned" && overview && <span className="ml-auto font-mono text-[10px] text-ink-faint">{overview.memories.length}</span>}
            </button>
          ))}
          <div className="mt-auto px-2 pb-1 font-mono text-[9.5px] leading-relaxed text-ink-faint">Tüm hafıza yalnızca bu Mac'te saklanır.</div>
        </nav>
        <section className="flex min-w-0 flex-1 flex-col">
          <header className="flex h-12 shrink-0 items-center border-b border-white/[0.06] px-5">
            <span className="hud-label flex-1 accent-text">{TABS.find((t) => t.id === tab)?.label}</span>
            <button type="button" onClick={() => setUi({ memoryOpen: false })} className="grid h-7 w-7 place-items-center rounded-md text-ink-dim hover:bg-white/[0.06] hover:text-white" aria-label="Kapat">
              <X size={14} />
            </button>
          </header>
          <div className="scroll-thin flex-1 overflow-y-auto px-5 py-5">
            {!overview ? (
              <div className="grid h-full place-items-center">
                <Loader2 size={16} className="animate-spin accent-text" />
              </div>
            ) : tab === "identity" || tab === "creator" ? (
              <IdentityEditor overview={overview} section={tab} onSaved={load} />
            ) : tab === "learned" ? (
              <LearnedTab overview={overview} onChanged={load} />
            ) : tab === "learning" ? (
              <LearningTab />
            ) : tab === "episodes" ? (
              <EpisodesTab overview={overview} />
            ) : (
              <SettingsTab overview={overview} />
            )}
          </div>
        </section>
      </motion.div>
    </motion.div>
  );
}
