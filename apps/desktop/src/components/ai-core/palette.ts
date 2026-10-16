import type { AIState } from "../../types/protocol";

export interface CoreLook {
  /** primary / highlight / deep tint */
  colors: [string, string, string];
  /** orbital speed multiplier */
  speed: number;
  /** overall activity: glow, displacement, stream visibility */
  energy: number;
  /** surface turbulence and particle agitation */
  turbulence: number;
  /** slow warning pulse (error only) */
  pulse: number;
  /** HUD label */
  label: string;
}

export const LOOKS: Record<"idle" | "listening" | "processing" | "speaking" | "error", CoreLook> = {
  idle: {
    colors: ["#00BFFF", "#00E5FF", "#0066FF"],
    speed: 0.35,
    energy: 0.28,
    turbulence: 0.18,
    pulse: 0,
    label: "BEKLEMEDE",
  },
  listening: {
    colors: ["#00E5FF", "#8CFBFF", "#00A3FF"],
    speed: 0.55,
    energy: 0.5,
    turbulence: 0.3,
    pulse: 0,
    label: "DİNLİYOR",
  },
  processing: {
    colors: ["#FFB300", "#FFD600", "#FF6D00"],
    speed: 1.45,
    energy: 0.78,
    turbulence: 0.85,
    pulse: 0,
    label: "İŞLENİYOR",
  },
  speaking: {
    colors: ["#00E676", "#00FF88", "#00B894"],
    speed: 0.75,
    energy: 0.62,
    turbulence: 0.4,
    pulse: 0,
    label: "KONUŞUYOR",
  },
  error: {
    colors: ["#FF1744", "#FF4D6A", "#B0002A"],
    speed: 0.22,
    energy: 0.45,
    turbulence: 0.3,
    pulse: 1,
    label: "HATA",
  },
};

export function lookFor(state: AIState): CoreLook {
  switch (state) {
    case "listening":
      return LOOKS.listening;
    case "transcribing":
    case "thinking":
      return LOOKS.processing;
    case "speaking":
      return LOOKS.speaking;
    case "error":
      return LOOKS.error;
    default:
      return LOOKS.idle;
  }
}

export function stateLabel(state: AIState, detail: string | null): string {
  if (state === "transcribing") return "ÇÖZÜMLÜYOR";
  if (state === "thinking" && detail === "research") return "ARAŞTIRIYOR";
  if (state === "thinking" && detail === "reasoning") return "DERİN DÜŞÜNÜYOR";
  if (state === "thinking" && detail === "memory") return "HATIRLIYOR";
  if (state === "thinking") return "DÜŞÜNÜYOR";
  return lookFor(state).label;
}
