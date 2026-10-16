import { useFrame, useThree } from "@react-three/fiber";
import { useMemo, useRef } from "react";
import {
  AdditiveBlending,
  BufferAttribute,
  BufferGeometry,
  CatmullRomCurve3,
  Color,
  EdgesGeometry,
  type Group,
  IcosahedronGeometry,
  Line,
  type Mesh,
  PlaneGeometry,
  RingGeometry,
  ShaderMaterial,
  TubeGeometry,
  Vector2,
  Vector3,
} from "three";

import { useApp } from "../../stores/app";
import { currentLevel } from "../../stores/audio";
import { lookFor } from "./palette";
import * as S from "./shaders";
import type { CoreUniforms } from "./uniforms";

export type Quality = "high" | "low";

function additive(
  uniforms: CoreUniforms,
  vertexShader: string,
  fragmentShader: string,
  extra: Record<string, { value: unknown }> = {},
): ShaderMaterial {
  return new ShaderMaterial({
    uniforms: { ...uniforms, ...extra },
    vertexShader,
    fragmentShader,
    transparent: true,
    depthWrite: false,
    blending: AdditiveBlending,
    premultipliedAlpha: true,
  });
}

// ── State driver: the only per-frame CPU work ─────────────────────────────
const tmpA = new Color();
const tmpB = new Color();
const tmpC = new Color();

function Driver({ u, reducedMotion }: { u: CoreUniforms; reducedMotion: boolean }) {
  const smooth = useRef({ speed: 0.35, level: 0 });
  const { camera, gl } = useThree();
  u.uPixelRatio.value = gl.getPixelRatio();

  useFrame((state, delta) => {
    const dt = Math.min(delta, 0.05);
    const { aiState } = useApp.getState();
    const look = lookFor(aiState);
    const k = 1 - Math.exp(-dt * 2.6);
    const motion = reducedMotion ? 0.35 : 1;
    u.uMotion.value += (motion - u.uMotion.value) * k;

    u.uColorA.value.lerp(tmpA.set(look.colors[0]), k);
    u.uColorB.value.lerp(tmpB.set(look.colors[1]), k);
    u.uColorC.value.lerp(tmpC.set(look.colors[2]), k);
    u.uEnergy.value += (look.energy - u.uEnergy.value) * k;
    u.uTurb.value += (look.turbulence - u.uTurb.value) * k;
    u.uPulse.value += (look.pulse - u.uPulse.value) * k;

    const s = smooth.current;
    s.speed += (look.speed - s.speed) * k;
    u.uPhase.value += dt * s.speed * u.uMotion.value;
    u.uTime.value += dt;

    // Real audio level: microphone while listening, synthesized voice while speaking.
    const raw =
      aiState === "speaking" ? currentLevel("tts") : aiState === "listening" ? currentLevel("mic") : currentLevel("tts") * 0.5;
    const target = reducedMotion ? raw * 0.4 : raw;
    const rate = target > s.level ? 22 : 7; // fast attack, slower release
    s.level += (target - s.level) * (1 - Math.exp(-dt * rate));
    u.uLevel.value = s.level;

    // Gentle cinematic parallax following the pointer.
    const px = reducedMotion ? 0 : state.pointer.x * 0.28;
    const py = reducedMotion ? 0 : state.pointer.y * 0.18;
    camera.position.x += (px - camera.position.x) * (1 - Math.exp(-dt * 1.6));
    camera.position.y += (py - camera.position.y) * (1 - Math.exp(-dt * 1.6));
    camera.lookAt(0, 0, 0);
  });
  return null;
}

// ── Backdrop ──────────────────────────────────────────────────────────────
function Backdrop({ u }: { u: CoreUniforms }) {
  const { size } = useThree();
  const mat = useMemo(
    () =>
      new ShaderMaterial({
        uniforms: { ...u, uAspect: { value: new Vector2(1, 1) }, uCenter: { value: new Vector2(0.5, 0.5) } },
        vertexShader: S.backdropVertex,
        fragmentShader: S.backdropFragment,
        depthWrite: false,
        depthTest: false,
      }),
    [u],
  );
  mat.uniforms.uAspect.value.set(size.width / size.height, 1);
  return (
    <mesh frustumCulled={false} renderOrder={-10} material={mat}>
      <planeGeometry args={[2, 2]} />
    </mesh>
  );
}

// ── Energy core: bright nucleus inside a translucent plasma shell ─────────
function EnergyCore({ u, quality }: { u: CoreUniforms; quality: Quality }) {
  const shell = useRef<Mesh>(null);
  const nucleus = useRef<Mesh>(null);
  const detail = quality === "high" ? 64 : 28;
  const shellGeo = useMemo(() => new IcosahedronGeometry(0.66, detail), [detail]);
  const nucleusGeo = useMemo(() => new IcosahedronGeometry(0.27, Math.round(detail / 2)), [detail]);
  const shellMat = useMemo(
    () => additive(u, S.coreVertex, S.shellPlasmaFragment, { uAmp: { value: 1 } }),
    [u],
  );
  const nucleusMat = useMemo(
    () =>
      new ShaderMaterial({
        uniforms: { ...u, uAmp: { value: 1.6 } },
        vertexShader: S.coreVertex,
        fragmentShader: S.nucleusFragment,
      }),
    [u],
  );
  useFrame(() => {
    const p = u.uPhase.value;
    shell.current?.rotation.set(Math.sin(p * 0.15) * 0.25, p * 0.35, 0);
    nucleus.current?.rotation.set(0, -p * 0.6, p * 0.2);
    nucleus.current?.scale.setScalar(1 + u.uLevel.value * 0.35 + Math.sin(u.uTime.value * 1.7) * 0.03);
  });
  return (
    <group>
      <mesh ref={nucleus} geometry={nucleusGeo} material={nucleusMat} />
      <mesh ref={shell} geometry={shellGeo} material={shellMat} />
    </group>
  );
}

function Glow({ u, size, strength, falloff }: { u: CoreUniforms; size: number; strength: number; falloff: number }) {
  const mat = useMemo(
    () => additive(u, S.glowVertex, S.glowFragment, { uStrength: { value: strength }, uFalloff: { value: falloff } }),
    [u, strength, falloff],
  );
  const geo = useMemo(() => new PlaneGeometry(size, size), [size]);
  return <mesh geometry={geo} material={mat} renderOrder={-1} />;
}

// ── Geodesic shell ────────────────────────────────────────────────────────
function Shell({ u }: { u: CoreUniforms }) {
  const ref = useRef<Group>(null);
  const inner = useMemo(() => new EdgesGeometry(new IcosahedronGeometry(0.92, 1)), []);
  const outer = useMemo(() => new EdgesGeometry(new IcosahedronGeometry(1.08, 0)), []);
  const matA = useMemo(() => additive(u, S.shellVertex, S.shellFragment, { uOpacity: { value: 0.55 } }), [u]);
  const matB = useMemo(() => additive(u, S.shellVertex, S.shellFragment, { uOpacity: { value: 0.3 } }), [u]);
  useFrame(() => {
    if (!ref.current) return;
    const p = u.uPhase.value;
    ref.current.children[0].rotation.set(p * 0.11, -p * 0.17, 0);
    ref.current.children[1].rotation.set(-p * 0.07, p * 0.09, p * 0.05);
    const s = 1 + u.uLevel.value * 0.06;
    ref.current.scale.setScalar(s);
  });
  return (
    <group ref={ref}>
      <lineSegments geometry={inner} material={matA} />
      <lineSegments geometry={outer} material={matB} />
    </group>
  );
}

// ── Holographic rings ─────────────────────────────────────────────────────
interface RingSpec {
  inner: number;
  outer: number;
  kind: number;
  segments: number;
  dir: number;
  spin: number;
  opacity: number;
  tilt: [number, number, number];
  react: number;
}

const RINGS: RingSpec[] = [
  { inner: 1.2, outer: 1.235, kind: 0, segments: 48, dir: 1, spin: 0.08, opacity: 0.9, tilt: [0, 0, 0], react: 0.05 },
  { inner: 1.32, outer: 1.42, kind: 1, segments: 120, dir: -1, spin: 0.03, opacity: 0.55, tilt: [0, 0, 0], react: 0.03 },
  { inner: 1.55, outer: 1.575, kind: 3, segments: 1, dir: 1, spin: 0.05, opacity: 0.7, tilt: [1.2, 0.2, 0], react: 0.06 },
  { inner: 1.7, outer: 1.78, kind: 2, segments: 1, dir: 1, spin: 0.22, opacity: 0.9, tilt: [0, 0, 0], react: 0.04 },
  { inner: 1.88, outer: 1.9, kind: 0, segments: 180, dir: -1, spin: 0.02, opacity: 0.6, tilt: [-1.05, -0.35, 0.2], react: 0.07 },
  { inner: 2.15, outer: 2.2, kind: 3, segments: 1, dir: -1, spin: 0.035, opacity: 0.45, tilt: [0.35, 0.9, 0], react: 0.05 },
  { inner: 2.45, outer: 2.53, kind: 1, segments: 240, dir: 1, spin: 0.012, opacity: 0.35, tilt: [0, 0, 0], react: 0.02 },
];

function Rings({ u }: { u: CoreUniforms }) {
  const group = useRef<Group>(null);
  const items = useMemo(
    () =>
      RINGS.map((r) => ({
        spec: r,
        geo: new RingGeometry(r.inner, r.outer, 256, 1),
        mat: additive(u, S.ringVertex, S.ringFragment, {
          uInner: { value: r.inner },
          uOuter: { value: r.outer },
          uKind: { value: r.kind },
          uSegments: { value: r.segments },
          uDir: { value: r.dir },
          uSpin: { value: r.spin },
          uOpacity: { value: r.opacity },
        }),
      })),
    [u],
  );
  useFrame(() => {
    const g = group.current;
    if (!g) return;
    const p = u.uPhase.value;
    const lvl = u.uLevel.value;
    g.children.forEach((child, i) => {
      const spec = items[i].spec;
      const precess = p * 0.04 * (i % 2 === 0 ? 1 : -1);
      child.rotation.set(spec.tilt[0] + Math.sin(p * 0.1 + i) * 0.04, spec.tilt[1] + precess, spec.tilt[2]);
      child.scale.setScalar(1 + lvl * spec.react * (i % 2 === 0 ? 1.4 : -0.6) + u.uEnergy.value * 0.015);
    });
  });
  return (
    <group ref={group}>
      {items.map((it, i) => (
        <mesh key={i} geometry={it.geo} material={it.mat} />
      ))}
    </group>
  );
}

// ── Audio waveform band ───────────────────────────────────────────────────
function Waveform({ u }: { u: CoreUniforms }) {
  const bands = useMemo(
    () =>
      [
        { base: 1.0, w: 0.012, seed: 0, opacity: 0.9 },
        { base: 1.035, w: 0.006, seed: 3.7, opacity: 0.55 },
        { base: 0.97, w: 0.006, seed: 7.1, opacity: 0.45 },
      ].map((b) => ({
        geo: new RingGeometry(b.base - b.w, b.base + b.w, 512, 1),
        mat: additive(u, S.waveVertex, S.waveFragment, {
          uBase: { value: b.base },
          uSeed: { value: b.seed },
          uOpacity: { value: b.opacity },
        }),
      })),
    [u],
  );
  return (
    <group>
      {bands.map((b, i) => (
        <mesh key={i} geometry={b.geo} material={b.mat} />
      ))}
    </group>
  );
}

// ── Particle field ────────────────────────────────────────────────────────
function Particles({ u, count }: { u: CoreUniforms; count: number }) {
  const geo = useMemo(() => {
    const g = new BufferGeometry();
    const radius = new Float32Array(count);
    const theta = new Float32Array(count);
    const y = new Float32Array(count);
    const speed = new Float32Array(count);
    const size = new Float32Array(count);
    const seed = new Float32Array(count);
    const tilt = new Float32Array(count);
    const pos = new Float32Array(count * 3);
    for (let i = 0; i < count; i++) {
      const band = Math.random() < 0.68;
      if (band) {
        // orbital disk with three tilted planes
        const plane = i % 3;
        radius[i] = 1.35 + Math.pow(Math.random(), 1.6) * 2.4;
        y[i] = (Math.random() - 0.5) * 0.12 * radius[i];
        tilt[i] = [0.18, -0.55, 1.05][plane] + (Math.random() - 0.5) * 0.05;
        speed[i] = 0.25 + Math.random() * 0.35;
      } else {
        // spherical cloud
        radius[i] = 1.15 + Math.random() * 1.6;
        y[i] = (Math.random() * 2 - 1) * radius[i] * 0.8;
        tilt[i] = Math.random() * Math.PI;
        speed[i] = 0.08 + Math.random() * 0.2;
      }
      theta[i] = Math.random() * Math.PI * 2;
      size[i] = 1.2 + Math.pow(Math.random(), 3) * 4.5;
      seed[i] = Math.random();
    }
    g.setAttribute("position", new BufferAttribute(pos, 3));
    g.setAttribute("aRadius", new BufferAttribute(radius, 1));
    g.setAttribute("aTheta", new BufferAttribute(theta, 1));
    g.setAttribute("aY", new BufferAttribute(y, 1));
    g.setAttribute("aSpeed", new BufferAttribute(speed, 1));
    g.setAttribute("aSize", new BufferAttribute(size, 1));
    g.setAttribute("aSeed", new BufferAttribute(seed, 1));
    g.setAttribute("aTilt", new BufferAttribute(tilt, 1));
    return g;
  }, [count]);
  const mat = useMemo(() => additive(u, S.particleVertex, S.particleFragment), [u]);
  return <points geometry={geo} material={mat} frustumCulled={false} />;
}

// ── Energy streams ────────────────────────────────────────────────────────
function randomDir(): Vector3 {
  return new Vector3(Math.random() * 2 - 1, Math.random() * 2 - 1, Math.random() * 2 - 1).normalize();
}

function Streams({ u, count }: { u: CoreUniforms; count: number }) {
  const ref = useRef<Group>(null);
  const items = useMemo(
    () =>
      Array.from({ length: count }, (_, i) => {
        const a = randomDir();
        const b = randomDir();
        const mid = a.clone().add(b).normalize();
        const reach = 1.35 + Math.random() * 1.0;
        const curve = new CatmullRomCurve3([
          a.clone().multiplyScalar(0.64),
          a.clone().lerp(mid, 0.5).normalize().multiplyScalar(reach * 0.85),
          mid.clone().multiplyScalar(reach),
          b.clone().lerp(mid, 0.5).normalize().multiplyScalar(reach * 0.85),
          b.clone().multiplyScalar(0.64),
        ]);
        return {
          geo: new TubeGeometry(curve, 96, 0.0045 + Math.random() * 0.004, 5, false),
          mat: additive(u, S.streamVertex, S.streamFragment, {
            uSeed: { value: i / count },
            uRate: { value: 0.18 + Math.random() * 0.25 },
          }),
        };
      }),
    [u, count],
  );
  useFrame(() => {
    if (ref.current) ref.current.rotation.set(u.uPhase.value * 0.05, u.uPhase.value * 0.13, 0);
  });
  return (
    <group ref={ref}>
      {items.map((it, i) => (
        <mesh key={i} geometry={it.geo} material={it.mat} />
      ))}
    </group>
  );
}

// ── Electrical arcs ───────────────────────────────────────────────────────
function Arcs({ u, count }: { u: CoreUniforms; count: number }) {
  const geo = useMemo(() => {
    const n = 28;
    const g = new BufferGeometry();
    const t = new Float32Array(n);
    for (let i = 0; i < n; i++) t[i] = i / (n - 1);
    g.setAttribute("position", new BufferAttribute(new Float32Array(n * 3), 3));
    g.setAttribute("aT", new BufferAttribute(t, 1));
    return g;
  }, []);
  const lines = useMemo(
    () =>
      Array.from({ length: count }, (_, i) => {
        const a = randomDir();
        const b = a.clone().add(randomDir().multiplyScalar(0.7)).normalize();
        const line = new Line(
          geo,
          additive(u, S.arcVertex, S.arcFragment, {
            uSeed: { value: i * 0.37 + Math.random() },
            uStart: { value: a },
            uEnd: { value: b },
          }),
        );
        line.frustumCulled = false;
        return line;
      }),
    [u, count, geo],
  );
  return (
    <group>
      {lines.map((l, i) => (
        <primitive key={i} object={l} />
      ))}
    </group>
  );
}

// ── Scan ring sweeping through the core while processing ─────────────────
function ScanRing({ u }: { u: CoreUniforms }) {
  const ref = useRef<Mesh>(null);
  const mat = useMemo(
    () =>
      additive(u, S.ringVertex, S.ringFragment, {
        uInner: { value: 0.98 },
        uOuter: { value: 1.0 },
        uKind: { value: 0 },
        uSegments: { value: 64 },
        uDir: { value: 1 },
        uSpin: { value: 0.3 },
        uOpacity: { value: 0 },
      }),
    [u],
  );
  const geo = useMemo(() => new RingGeometry(0.98, 1.0, 128, 1), []);
  useFrame(() => {
    const m = ref.current;
    if (!m) return;
    const y = Math.sin(u.uTime.value * 1.3 * u.uMotion.value) * 0.62;
    m.position.y = y;
    m.scale.setScalar(Math.sqrt(Math.max(0.0001, 0.7 * 0.7 - y * y)) / 0.7 * 0.74);
    mat.uniforms.uOpacity.value = Math.max(0, (u.uEnergy.value - 0.55) * 3.2);
  });
  return <mesh ref={ref} geometry={geo} material={mat} rotation={[Math.PI / 2, 0, 0]} />;
}

export function CoreScene({
  u,
  quality,
  reducedMotion,
}: {
  u: CoreUniforms;
  quality: Quality;
  reducedMotion: boolean;
}) {
  const high = quality === "high";
  return (
    <>
      <Driver u={u} reducedMotion={reducedMotion} />
      <Backdrop u={u} />
      <Glow u={u} size={6} strength={0.32} falloff={6} />
      <Glow u={u} size={2.3} strength={0.6} falloff={7.5} />
      <Particles u={u} count={high ? 4200 : 1800} />
      <Streams u={u} count={high ? 9 : 5} />
      <Rings u={u} />
      <Waveform u={u} />
      <Shell u={u} />
      <Arcs u={u} count={high ? 5 : 3} />
      <ScanRing u={u} />
      <EnergyCore u={u} quality={quality} />
    </>
  );
}
