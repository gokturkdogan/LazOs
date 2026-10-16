import { PerformanceMonitor } from "@react-three/drei";
import { Canvas } from "@react-three/fiber";
import { Bloom, EffectComposer, Vignette } from "@react-three/postprocessing";
import { memo, useMemo, useState } from "react";

import { useApp } from "../../stores/app";
import { CoreScene, type Quality } from "./CoreScene";
import { createCoreUniforms } from "./uniforms";

/**
 * The living LAZOS core. Rendered in its own canvas; state flows in through the zustand store and
 * the audio level buffer (read inside useFrame), so this component never re-renders per frame.
 */
export const AICore = memo(function AICore() {
  const u = useMemo(createCoreUniforms, []);
  const preference = useApp((s) => s.settings?.visual_quality ?? "auto");
  const reducedSetting = useApp((s) => s.settings?.reduced_motion ?? false);
  const prefersReduced = useMemo(() => window.matchMedia("(prefers-reduced-motion: reduce)").matches, []);
  const [autoQuality, setAutoQuality] = useState<Quality>("high");
  const [dpr, setDpr] = useState(1.5);

  const quality: Quality = preference === "auto" ? autoQuality : preference;
  const reducedMotion = reducedSetting || prefersReduced;

  return (
    <Canvas
      dpr={quality === "high" ? dpr : 1}
      camera={{ position: [0, 0, 7.4], fov: 42, near: 0.1, far: 50 }}
      gl={{ antialias: false, powerPreference: "high-performance", alpha: false, stencil: false }}
      style={{ position: "absolute", inset: 0 }}
    >
      <PerformanceMonitor
        bounds={() => [50, 58]}
        flipflops={3}
        onDecline={() => {
          if (dpr > 1) setDpr(1);
          else setAutoQuality("low");
        }}
        onIncline={() => setDpr((d) => Math.min(1.75, d + 0.25))}
      />
      <CoreScene u={u} quality={quality} reducedMotion={reducedMotion} />
      <EffectComposer multisampling={0} enableNormalPass={false}>
        <Bloom mipmapBlur intensity={0.9} luminanceThreshold={0.14} luminanceSmoothing={0.3} radius={0.7} />
        <Vignette eskil={false} offset={0.28} darkness={0.72} />
      </EffectComposer>
    </Canvas>
  );
});
