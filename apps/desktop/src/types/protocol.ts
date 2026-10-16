/**
 * Typed contract between the LAZOS UI and the local AI backend.
 * Mirrors services/ai-backend/app/api/schemas.py (client → server) and the events
 * published by the backend (server → client).
 */

export type AIState = "idle" | "listening" | "transcribing" | "thinking" | "speaking" | "error";
export type VoiceMode = "off" | "ptt" | "continuous";

export interface Source {
  id: number;
  title: string;
  url: string;
  domain: string;
  snippet: string;
  provider: string;
  published?: string | null;
  cited?: boolean;
}

export interface TurnMetrics {
  origin?: string;
  stt_ms?: number;
  research_ms?: number;
  ttft_ms?: number;
  tokens?: number;
  tokens_per_s?: number;
  prompt_tokens?: number;
  tts_first_audio_ms?: number;
  total_ms?: number;
  reasoning?: boolean;
  examples?: number;
  evidence?: "verified" | "single_source" | "conflict" | "insufficient";
}

export interface ResearchCandidate {
  title: string;
  url: string;
  domain: string;
  published: string | null;
  snippet: string;
  score: number;
  selected: boolean;
  reasons: string[];
  rejected: string | null;
}

export interface ResearchFetch {
  url: string;
  status: string;
  fetch_ms?: number;
  extract_ms?: number;
  bytes?: number;
  tables?: number;
  json_tables?: number;
  text_chars?: number;
  published?: string | null;
  standings_rows?: number;
  rejected_tables?: string[];
}

export interface StandingRow {
  rank: number;
  team: string;
  played: number | null;
  won: number | null;
  drawn: number | null;
  lost: number | null;
  goal_diff: number | null;
  points: number | null;
}

export interface ResearchEvidence {
  question_type: string;
  competition: string;
  season: string;
  want: string;
  entity: string | null;
  status: "verified" | "single_source" | "conflict" | "insufficient";
  row: StandingRow | null;
  table: StandingRow[];
  source_url: string | null;
  agreeing: string[];
  notes: string[];
  retrieved_at: string;
}

export interface ResearchTrace {
  request: string;
  intent: { kind: string; want: string; entity: string | null; competition: string | null; season: string | null };
  queries: string[];
  provider: string | null;
  candidates: ResearchCandidate[];
  fetches: ResearchFetch[];
  evidence: ResearchEvidence | null;
  context: string;
  answer: string;
  message_id: string | null;
  timings_ms: Record<string, number>;
  fallbacks: string[];
  errors: string[];
  created_at: number;
}

export interface ChatMessage {
  id: string;
  conversation_id: string;
  role: "user" | "assistant";
  content: string;
  created_at: number;
  origin: "text" | "voice";
  status: "complete" | "interrupted" | "streaming" | "error";
  sources: Source[];
  metrics?: TurnMetrics | null;
  research?: ResearchProgress | null;
  error?: string | null;
  feedback?: MessageFeedback | null;
  correction?: ResponseCorrection | null;
  stt_original?: string | null;
}

// ── adaptive learning (V1.2.1) ────────────────────────────────────────────
export type FeedbackReason =
  | "incorrect"
  | "unnatural_turkish"
  | "too_long"
  | "too_short"
  | "misunderstood"
  | "lost_context"
  | "wrong_tone"
  | "unnecessary_tool"
  | "other";

export interface MessageFeedback {
  id: string;
  message_id: string;
  rating: 1 | -1;
  reasons: FeedbackReason[];
  note: string | null;
  model: string | null;
  mode: Record<string, unknown> | null;
  updated_at: number;
}

export interface ResponseCorrection {
  id: string;
  message_id: string | null;
  conversation_id: string | null;
  user_prompt: string;
  original_response: string;
  preferred_response: string;
  kind: "style" | "local" | "factual";
  status: "pending" | "approved" | "rejected";
  blocked_reason: string | null;
  usable_as_example: boolean;
  updated_at: number;
}

export interface LearningSuggestion {
  id: string;
  kind: "preference" | "stt_term";
  signal: string;
  proposal: string;
  category: string | null;
  evidence: string[];
  status: "pending" | "approved" | "rejected" | "revoked" | "superseded";
  memory_id: string | null;
  note: string | null;
  updated_at: number;
}

export interface TranscriptCorrection {
  id: string;
  message_id: string | null;
  original_text: string;
  corrected_text: string;
  terms: string[];
  created_at: number;
}

export interface LearningOverview {
  enabled: boolean;
  reasons: Record<FeedbackReason, string>;
  stats: {
    feedback_total: number;
    positive: number;
    negative: number;
    corrections: number;
    approved_examples: number;
    pending_suggestions: number;
    transcript_corrections: number;
    reasons: Partial<Record<FeedbackReason, number>>;
  };
  corrections: ResponseCorrection[];
  suggestions: LearningSuggestion[];
  transcripts: TranscriptCorrection[];
}

export interface TrainingPreview {
  records: { id: string; instruction: string; preferred_response: string; warnings: string[] }[];
  excluded: { id: string; reason: string }[];
  warnings: number;
}

export interface ResearchProgress {
  status: "searching" | "done" | "failed";
  query: string;
  provider?: string | null;
  error?: string | null;
  sources?: Source[];
}

export interface ConversationSummary {
  id: string;
  title: string;
  created_at: number;
  updated_at: number;
  message_count: number;
}

export interface ConversationDetail extends ConversationSummary {
  messages: ChatMessage[];
}

export interface UserSettings {
  llm_model: string;
  temperature: number;
  num_ctx: number;
  max_response_tokens: number;
  keep_alive_minutes: number;
  research_mode: "auto" | "always" | "off";
  max_sources: number;
  stt_language: "auto" | "tr" | "en";
  tts_engine: "piper" | "macos";
  tts_speed: number;
  speak_text_replies: boolean;
  pronunciation_enabled: boolean;
  stt_vocabulary: boolean;
  conversation_style: "natural" | "professional" | "concise";
  voice_style: "natural" | "detailed";
  reasoning_mode: "fast" | "auto" | "deep";
  vad_threshold: number;
  vad_silence_ms: number;
  input_device: string | null;
  output_device: string | null;
  memory_enabled: boolean;
  memory_auto_learn: boolean;
  feedback_examples_enabled: boolean;
  research_diagnostics: boolean;
  reduced_motion: boolean;
  visual_quality: "auto" | "high" | "low";
}

// ── identity & memory ─────────────────────────────────────────────────────
export interface PronunciationRule {
  id: string;
  term: string;
  replacement: string;
  engine: "all" | "piper" | "macos";
  case_sensitive: boolean;
  enabled: boolean;
  builtin: boolean;
  verified: boolean;
}

export interface PronunciationOverview {
  enabled: boolean;
  engines: ("piper" | "macos")[];
  builtin: PronunciationRule[];
  custom: PronunciationRule[];
}

export interface CoreIdentity {
  name: string;
  creator_name: string;
  origin: string;
  purpose: string;
  creator_description: string;
  identity_description: string;
}

export type MemoryCategory =
  | "user_identity"
  | "preference"
  | "communication"
  | "project"
  | "interest"
  | "decision"
  | "knowledge";

export interface MemoryItem {
  id: string;
  category: MemoryCategory;
  category_label: string;
  content: string;
  origin: "auto" | "explicit" | "manual" | "feedback";
  sensitive: boolean;
  source_conversation_id: string | null;
  source_message_id: string | null;
  history: { content: string; until: number }[];
  created_at: number;
  updated_at: number;
  use_count: number;
  last_used_at: number | null;
}

export interface MemoryOverview {
  identity: CoreIdentity;
  default_identity: CoreIdentity;
  categories: Record<MemoryCategory, string>;
  profile_categories: MemoryCategory[];
  memories: MemoryItem[];
  stats: { memories: number; by_category: Partial<Record<MemoryCategory, number>>; conversations: number; indexed_messages: number };
  pending_learning: number;
}

export interface EpisodeHit {
  conversation_id: string;
  conversation_title: string;
  message_id: string;
  role: "user" | "assistant";
  content: string;
  created_at: number;
}

export interface SystemStatus {
  version: string;
  llm: { reachable: boolean; version?: string | null; model?: string; loaded?: boolean; error?: string | null };
  stt: { available: boolean; loaded: boolean; model: string };
  tts: { preferred: string; engines: Record<string, boolean> };
  vad: { available: boolean };
  online: boolean | null;
  search_provider: string | null;
  models_ready: boolean;
  last_turn: TurnMetrics | null;
}

export type ModelStatus = "present" | "missing" | "downloading" | "converting" | "verifying" | "error" | "unknown";

export interface ModelInfo {
  id: "llm" | "stt" | "tts" | "vad";
  kind: string;
  label: string;
  description: string;
  required: boolean;
  approx_bytes: number;
  download_bytes: number;
  status: ModelStatus;
  progress: number;
  detail: string;
  error: string | null;
  source: "download" | "local-convert" | "ollama" | null;
}

export interface SetupInfo {
  system: {
    os: string;
    arch: string;
    chip: string;
    memory_bytes: number;
    cpu_count: number;
    apple_silicon: boolean;
    disk_free_bytes: number;
    compatible: boolean;
  };
  runtime: { ollama_installed: boolean; ollama_running: boolean; ollama_version: string | null };
  models: ModelInfo[];
  audio: { inputs: number; outputs: number; error?: string | null };
  search: { name: string; available: boolean }[];
  ready: boolean;
}

export interface AudioDevice {
  index: number;
  name: string;
  default: boolean;
}

// ── server → client events ────────────────────────────────────────────────
export type ServerEvent =
  | { type: "hello"; conversation_id: string | null; settings: UserSettings }
  | { type: "pong" }
  | { type: "state"; state: AIState; detail: string | null }
  | { type: "system" } & SystemStatus
  | { type: "settings"; settings: UserSettings }
  | { type: "setup.models"; models: ModelInfo[] }
  | { type: "voice.mode"; mode: VoiceMode }
  | { type: "voice.speech"; active: boolean }
  | { type: "voice.transcript"; text: string; language: string; stt_ms: number }
  | { type: "voice.notice"; level: "info" | "error"; message: string }
  | { type: "audio.level"; source: "mic" | "tts"; level: number }
  | { type: "chat.user"; conversation: ConversationSummary; message: ChatMessage }
  | { type: "chat.start"; conversation_id: string; message_id: string }
  | { type: "chat.delta"; message_id: string; text: string }
  | ({ type: "chat.research"; message_id: string } & ResearchProgress)
  | { type: "chat.done"; message_id: string; status: string; message: ChatMessage | null }
  | { type: "chat.error"; message_id?: string; code: string; message: string }
  | { type: "metrics"; turn: TurnMetrics }
  | {
      type: "memory.changed";
      change: "added" | "updated" | "deleted" | "cleared";
      origin: "auto" | "explicit" | "manual" | "feedback";
      memory: MemoryItem | null;
    }
  | { type: "identity.changed"; identity: CoreIdentity }
  | { type: "learning.changed"; what: string }
  | { type: "learning.suggestion"; kind: "preference" | "stt_term"; proposal: string }
  | { type: "error"; code: string; message: string };

// ── client → server messages ──────────────────────────────────────────────
export type ClientMessage =
  | { type: "chat.send"; text: string; conversation_id: string | null }
  | { type: "chat.cancel" }
  | { type: "speech.stop" }
  | { type: "voice.ptt"; action: "start" | "stop"; conversation_id: string | null }
  | { type: "voice.continuous"; enabled: boolean; conversation_id: string | null }
  | { type: "conversation.select"; conversation_id: string | null }
  | { type: "ping" };
