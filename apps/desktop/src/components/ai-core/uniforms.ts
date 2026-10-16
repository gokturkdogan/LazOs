import { Color } from "three";

/**
 * One uniform set shared by every material of the core. Uniform objects are shared by reference,
 * so a single update per frame drives the whole entity.
 */
export function createCoreUniforms() {
  return {
    uTime: { value: 0 },
    uPhase: { value: 0 },
    uColorA: { value: new Color("#00BFFF") },
    uColorB: { value: new Color("#00E5FF") },
    uColorC: { value: new Color("#0066FF") },
    uEnergy: { value: 0.28 },
    uLevel: { value: 0 },
    uTurb: { value: 0.18 },
    uPulse: { value: 0 },
    uMotion: { value: 1 },
    uPixelRatio: { value: 1 },
  };
}

export type CoreUniforms = ReturnType<typeof createCoreUniforms>;
