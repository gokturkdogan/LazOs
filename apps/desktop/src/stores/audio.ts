/**
 * High-frequency audio levels live outside React state: the 3D core and meters read them every
 * animation frame, so routing ~60 updates/s through React would cause needless re-renders.
 */
export const audioLevels = {
  mic: 0,
  tts: 0,
  micAt: 0,
  ttsAt: 0,
};

export function setLevel(source: "mic" | "tts", level: number): void {
  const now = performance.now();
  if (source === "mic") {
    audioLevels.mic = level;
    audioLevels.micAt = now;
  } else {
    audioLevels.tts = level;
    audioLevels.ttsAt = now;
  }
}

/** Levels decay to zero if updates stop arriving (e.g. connection loss). */
export function currentLevel(source: "mic" | "tts"): number {
  const at = source === "mic" ? audioLevels.micAt : audioLevels.ttsAt;
  const value = source === "mic" ? audioLevels.mic : audioLevels.tts;
  return performance.now() - at > 400 ? 0 : value;
}
