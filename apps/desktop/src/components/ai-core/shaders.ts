/* GLSL sources for the LAZOS living core. */

export const NOISE = /* glsl */ `
// 3D simplex noise — Ashima Arts / Stefan Gustavson (MIT)
vec3 mod289(vec3 x){return x-floor(x*(1.0/289.0))*289.0;}
vec4 mod289(vec4 x){return x-floor(x*(1.0/289.0))*289.0;}
vec4 permute(vec4 x){return mod289(((x*34.0)+1.0)*x);}
vec4 taylorInvSqrt(vec4 r){return 1.79284291400159-0.85373472095314*r;}
float snoise(vec3 v){
  const vec2 C=vec2(1.0/6.0,1.0/3.0);
  const vec4 D=vec4(0.0,0.5,1.0,2.0);
  vec3 i=floor(v+dot(v,C.yyy));
  vec3 x0=v-i+dot(i,C.xxx);
  vec3 g=step(x0.yzx,x0.xyz);
  vec3 l=1.0-g;
  vec3 i1=min(g.xyz,l.zxy);
  vec3 i2=max(g.xyz,l.zxy);
  vec3 x1=x0-i1+C.xxx;
  vec3 x2=x0-i2+C.yyy;
  vec3 x3=x0-D.yyy;
  i=mod289(i);
  vec4 p=permute(permute(permute(i.z+vec4(0.0,i1.z,i2.z,1.0))+i.y+vec4(0.0,i1.y,i2.y,1.0))+i.x+vec4(0.0,i1.x,i2.x,1.0));
  float n_=0.142857142857;
  vec3 ns=n_*D.wyz-D.xzx;
  vec4 j=p-49.0*floor(p*ns.z*ns.z);
  vec4 x_=floor(j*ns.z);
  vec4 y_=floor(j-7.0*x_);
  vec4 x=x_*ns.x+ns.yyyy;
  vec4 y=y_*ns.x+ns.yyyy;
  vec4 h=1.0-abs(x)-abs(y);
  vec4 b0=vec4(x.xy,y.xy);
  vec4 b1=vec4(x.zw,y.zw);
  vec4 s0=floor(b0)*2.0+1.0;
  vec4 s1=floor(b1)*2.0+1.0;
  vec4 sh=-step(h,vec4(0.0));
  vec4 a0=b0.xzyw+s0.xzyw*sh.xxyy;
  vec4 a1=b1.xzyw+s1.xzyw*sh.zzww;
  vec3 p0=vec3(a0.xy,h.x);
  vec3 p1=vec3(a0.zw,h.y);
  vec3 p2=vec3(a1.xy,h.z);
  vec3 p3=vec3(a1.zw,h.w);
  vec4 norm=taylorInvSqrt(vec4(dot(p0,p0),dot(p1,p1),dot(p2,p2),dot(p3,p3)));
  p0*=norm.x;p1*=norm.y;p2*=norm.z;p3*=norm.w;
  vec4 m=max(0.6-vec4(dot(x0,x0),dot(x1,x1),dot(x2,x2),dot(x3,x3)),0.0);
  m=m*m;
  return 42.0*dot(m*m,vec4(dot(p0,x0),dot(p1,x1),dot(p2,x2),dot(p3,x3)));
}
float hash11(float p){p=fract(p*0.1031);p*=p+33.33;p*=p+p;return fract(p);}
`;

const COMMON_UNIFORMS = /* glsl */ `
uniform float uTime;
uniform float uPhase;
uniform vec3 uColorA;
uniform vec3 uColorB;
uniform vec3 uColorC;
uniform float uEnergy;
uniform float uLevel;
uniform float uTurb;
uniform float uPulse;
uniform float uMotion;
uniform float uPixelRatio;
`;

// ── Energy core: noise-displaced plasma sphere with fresnel rim ───────────
export const coreVertex = /* glsl */ `
${COMMON_UNIFORMS}
${NOISE}
uniform float uAmp;
varying vec3 vNormal;
varying vec3 vView;
varying vec3 vPos;
varying float vDisp;
void main(){
  float t = uTime * uMotion;
  vec3 n = normalize(normal);
  float n1 = snoise(n * 1.4 + vec3(t * (0.16 + 0.32 * uTurb)));
  float n2 = snoise(n * 3.8 - vec3(t * (0.28 + 0.55 * uTurb)));
  float breath = sin(t * 0.9) * 0.01;
  float disp = (n1 * (0.022 + 0.05 * uEnergy) + n2 * 0.018 * uTurb + uLevel * (0.07 + 0.06 * n1) + breath) * uAmp;
  vec3 p = position + n * disp;
  vDisp = disp;
  vPos = p;
  vec4 mv = modelViewMatrix * vec4(p, 1.0);
  vNormal = normalize(normalMatrix * n);
  vView = normalize(-mv.xyz);
  gl_Position = projectionMatrix * mv;
}`;

// Translucent plasma shell: glowing fresnel edge + drifting energy filaments.
export const shellPlasmaFragment = /* glsl */ `
${COMMON_UNIFORMS}
${NOISE}
varying vec3 vNormal;
varying vec3 vView;
varying vec3 vPos;
varying float vDisp;
void main(){
  float t = uTime * uMotion;
  float facing = max(dot(vNormal, vView), 0.0);
  float fres = pow(1.0 - facing, 2.2);
  float f1 = snoise(vPos * 3.0 + vec3(0.0, t * 0.45, t * 0.2));
  float f2 = snoise(vPos * 6.5 - vec3(t * 0.35));
  float filaments = pow(1.0 - abs(f1), 6.0) * 0.9 + pow(1.0 - abs(f2), 10.0) * 0.5;
  float pulse = 1.0 + uPulse * (0.35 + 0.35 * sin(uTime * 3.1));
  vec3 col = uColorB * fres * (1.35 + uLevel * 1.6) + uColorA * filaments * (0.35 + uEnergy * 0.75 + uLevel);
  col += uColorC * 0.06;
  float a = clamp(fres * 0.95 + filaments * 0.45 + 0.04, 0.0, 1.0);
  col *= pulse;
  gl_FragColor = vec4(col * a, a);
}`;

// Bright nucleus at the heart of the core.
export const nucleusFragment = /* glsl */ `
${COMMON_UNIFORMS}
${NOISE}
varying vec3 vNormal;
varying vec3 vView;
varying vec3 vPos;
varying float vDisp;
void main(){
  float t = uTime * uMotion;
  float facing = max(dot(vNormal, vView), 0.0);
  float plasma = snoise(vPos * 5.0 + vec3(t * 0.6, -t * 0.4, t * 0.3)) * 0.5 + 0.5;
  vec3 hot = mix(uColorB, vec3(1.0), 0.55 + 0.25 * facing);
  vec3 col = mix(uColorA * 1.2, hot, smoothstep(0.15, 0.9, facing));
  col *= 1.15 + plasma * 0.55 + uLevel * 1.4 + uEnergy * 0.4;
  col *= 1.0 + uPulse * (0.3 + 0.3 * sin(uTime * 3.1));
  gl_FragColor = vec4(col, 1.0);
}`;

// ── Soft volumetric glow (camera-facing quad) ─────────────────────────────
export const glowVertex = /* glsl */ `
varying vec2 vUv;
void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`;

export const glowFragment = /* glsl */ `
${COMMON_UNIFORMS}
uniform float uStrength;
uniform float uFalloff;
varying vec2 vUv;
void main(){
  float d = length(vUv - 0.5) * 2.0;
  float g = exp(-d * d * uFalloff);
  float pulse = 1.0 + uPulse * (0.3 + 0.3 * sin(uTime * 3.1));
  float k = g * uStrength * (0.55 + uEnergy * 0.6 + uLevel * 0.9) * pulse;
  vec3 col = mix(uColorC, uColorA, g) * k;
  gl_FragColor = vec4(col, k);
}`;

// ── Holographic rings: procedural segments, ticks and sweeps ──────────────
export const ringVertex = /* glsl */ `
varying vec2 vLocal;
void main(){ vLocal = position.xy; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`;

export const ringFragment = /* glsl */ `
${COMMON_UNIFORMS}
uniform float uInner;
uniform float uOuter;
uniform float uKind;      // 0 dashes, 1 ticks, 2 sweep, 3 broken line
uniform float uSegments;
uniform float uDir;
uniform float uSpin;
uniform float uOpacity;
varying vec2 vLocal;
#define TAU 6.28318530718
void main(){
  float r = length(vLocal);
  float radial = clamp((r - uInner) / (uOuter - uInner), 0.0, 1.0);
  float edge = smoothstep(0.0, 0.18, radial) * smoothstep(1.0, 0.82, radial);
  float a = atan(vLocal.y, vLocal.x) / TAU + 0.5;
  float rot = uPhase * uSpin * uDir;
  float ang = fract(a + rot);
  float pattern = 0.0;
  if (uKind < 0.5) {
    float seg = fract(ang * uSegments);
    pattern = step(seg, 0.62) * (0.65 + 0.35 * step(fract(ang * 3.0), 0.5));
  } else if (uKind < 1.5) {
    float seg = fract(ang * uSegments);
    float major = step(fract(ang * uSegments / 6.0), 1.0 / 6.0);
    float tick = step(seg, 0.18);
    pattern = tick * mix(step(radial, 0.55), 1.0, major);
    edge = 1.0;
  } else if (uKind < 2.5) {
    float head = fract(ang);
    pattern = pow(head, 6.0) * 1.6 + step(0.995, head) * 2.0;
    pattern += 0.08;
  } else {
    float gap = step(0.04, fract(ang * 4.0));
    pattern = gap * (0.55 + 0.45 * smoothstep(0.3, 0.7, fract(ang * 2.0 + 0.25)));
  }
  float shimmer = 0.85 + 0.15 * sin(uTime * 2.0 + a * 40.0);
  float alpha = pattern * edge * uOpacity * shimmer * (0.4 + uEnergy * 0.6 + uLevel * 0.8);
  vec3 col = mix(uColorA, uColorB, ang) * (1.2 + uLevel);
  gl_FragColor = vec4(col * alpha, alpha);
}`;

// ── Audio waveform band ───────────────────────────────────────────────────
export const waveVertex = /* glsl */ `
${COMMON_UNIFORMS}
${NOISE}
uniform float uBase;
uniform float uSeed;
varying float vAmp;
void main(){
  float ang = atan(position.y, position.x);
  float r = length(position.xy);
  float t = uTime * uMotion;
  float spikes = abs(snoise(vec3(cos(ang) * 1.6, sin(ang) * 1.6, t * 2.2 + uSeed)));
  float ripple = sin(ang * 12.0 + t * 3.0 + uSeed) * 0.5 + 0.5;
  float amp = uLevel * (0.08 + 0.32 * spikes) + 0.012 * ripple * (0.3 + uEnergy);
  vAmp = amp;
  vec3 p = vec3(normalize(position.xy) * (r + amp * (r > uBase ? 1.0 : -0.35)), 0.0);
  gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 1.0);
}`;

export const waveFragment = /* glsl */ `
${COMMON_UNIFORMS}
uniform float uOpacity;
varying float vAmp;
void main(){
  float a = uOpacity * (0.25 + uLevel * 1.4 + vAmp * 3.0);
  vec3 col = mix(uColorA, uColorB, clamp(vAmp * 5.0, 0.0, 1.0)) * 1.4;
  gl_FragColor = vec4(col * a, a);
}`;

// ── Orbiting particle field ───────────────────────────────────────────────
export const particleVertex = /* glsl */ `
${COMMON_UNIFORMS}
${NOISE}
attribute float aRadius;
attribute float aTheta;
attribute float aY;
attribute float aSpeed;
attribute float aSize;
attribute float aSeed;
attribute float aTilt;
varying float vAlpha;
varying float vMix;
void main(){
  float t = uTime * uMotion;
  float th = aTheta + uPhase * aSpeed * (1.6 / aRadius);
  float y = aY + sin(t * 0.35 + aSeed * 6.2831) * 0.06;
  vec3 p = vec3(cos(th) * aRadius, y, sin(th) * aRadius);
  float ct = cos(aTilt), st = sin(aTilt);
  p = vec3(p.x, p.y * ct - p.z * st, p.y * st + p.z * ct);
  vec3 drift = vec3(
    snoise(p * 0.7 + vec3(t * 0.25, 0.0, aSeed)),
    snoise(p * 0.7 + vec3(0.0, t * 0.25, aSeed + 7.0)),
    snoise(p * 0.7 + vec3(aSeed + 13.0, 0.0, t * 0.25))
  );
  p += drift * (0.04 + 0.22 * uTurb);
  p *= 1.0 + uLevel * 0.14 * (0.4 + aSeed);
  vec4 mv = modelViewMatrix * vec4(p, 1.0);
  gl_Position = projectionMatrix * mv;
  float twinkle = 0.55 + 0.45 * sin(t * (1.5 + aSeed * 3.0) + aSeed * 40.0);
  vAlpha = twinkle * (0.35 + uEnergy * 0.65) * smoothstep(4.2, 1.2, aRadius);
  vMix = aSeed;
  gl_PointSize = aSize * (1.0 + uEnergy * 0.6 + uLevel * 1.2) * uPixelRatio * (6.0 / -mv.z);
}`;

export const particleFragment = /* glsl */ `
${COMMON_UNIFORMS}
varying float vAlpha;
varying float vMix;
void main(){
  vec2 c = gl_PointCoord - 0.5;
  float d = length(c);
  float soft = smoothstep(0.5, 0.0, d);
  float core = smoothstep(0.18, 0.0, d);
  float a = (soft * 0.55 + core) * vAlpha;
  vec3 col = mix(uColorA, uColorB, vMix) * (1.1 + core);
  gl_FragColor = vec4(col * a, a);
}`;

// ── Energy streams: comets travelling along curved filaments ──────────────
export const streamVertex = /* glsl */ `
varying vec2 vUv;
void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`;

export const streamFragment = /* glsl */ `
${COMMON_UNIFORMS}
uniform float uSeed;
uniform float uRate;
varying vec2 vUv;
void main(){
  float t = uTime * uMotion;
  float head = fract(t * uRate * (0.4 + uEnergy) + uSeed);
  float d = head - vUv.x;
  float comet = d >= 0.0 ? exp(-d * 9.0) : 0.0;
  float base = 0.05;
  float a = (base + comet * (0.45 + uEnergy * 1.1)) * (0.2 + uEnergy * 1.0) * smoothstep(0.0, 0.08, vUv.x) * smoothstep(1.0, 0.92, vUv.x);
  vec3 col = mix(uColorA, uColorB, comet) * 1.25;
  gl_FragColor = vec4(col * a, a);
}`;

// ── Electrical arcs (jittered polylines that flicker in short bursts) ─────
export const arcVertex = /* glsl */ `
${COMMON_UNIFORMS}
${NOISE}
attribute float aT;
uniform float uSeed;
uniform vec3 uStart;
uniform vec3 uEnd;
varying float vVis;
void main(){
  float frame = floor(uTime * 14.0);
  // Arcs only crackle when LAZOS is busy (processing/error); idle and listening stay calm.
  float duty = max(0.0, uTurb - 0.35) * 0.32 + uPulse * 0.08;
  float burst = step(fract(uTime * (0.2 + uTurb * 0.7) + uSeed), duty);
  vec3 dir = normalize(mix(uStart, uEnd, aT));
  float lift = sin(3.14159 * aT) * 0.14;
  vec3 p = dir * (0.64 + lift);
  float j = (hash11(frame * 13.1 + aT * 97.0 + uSeed * 31.0) - 0.5) * 0.07 * sin(3.14159 * aT);
  vec3 side = normalize(cross(dir, vec3(0.0, 1.0, 0.2)));
  p += side * j + dir * j * 0.5;
  vVis = burst;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 1.0);
}`;

export const arcFragment = /* glsl */ `
${COMMON_UNIFORMS}
varying float vVis;
void main(){
  float a = vVis * (0.15 + uTurb * 0.7 + uPulse * 0.5);
  vec3 col = mix(uColorB, vec3(1.0), 0.2) * 1.4;
  gl_FragColor = vec4(col * a, a);
}`;

// ── Geodesic shell lines ──────────────────────────────────────────────────
export const shellVertex = /* glsl */ `
${COMMON_UNIFORMS}
varying float vFacing;
varying vec3 vPos;
void main(){
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  vec3 n = normalize(normalMatrix * normalize(position));
  vFacing = abs(dot(n, normalize(-mv.xyz)));
  vPos = position;
  gl_Position = projectionMatrix * mv;
}`;

export const shellFragment = /* glsl */ `
${COMMON_UNIFORMS}
uniform float uOpacity;
varying float vFacing;
varying vec3 vPos;
void main(){
  float scan = smoothstep(0.08, 0.0, abs(fract(vPos.y * 0.6 - uTime * 0.25 * uMotion) - 0.5) - 0.42);
  float rim = pow(1.0 - vFacing, 1.6);
  float a = uOpacity * (0.18 + rim * 0.6 + scan * 0.5 * uEnergy) * (0.6 + uEnergy * 0.6);
  vec3 col = mix(uColorC, uColorB, rim) * 1.2;
  gl_FragColor = vec4(col * a, a);
}`;

// ── Backdrop: deep space gradient tinted by the state colour ──────────────
export const backdropVertex = /* glsl */ `
varying vec2 vUv;
void main(){ vUv = uv; gl_Position = vec4(position.xy, 0.9999, 1.0); }`;

export const backdropFragment = /* glsl */ `
${COMMON_UNIFORMS}
${NOISE}
uniform vec2 uAspect;
uniform vec2 uCenter;
varying vec2 vUv;
void main(){
  vec2 p = (vUv - uCenter) * uAspect;
  float d = length(p);
  vec3 base = mix(vec3(0.012, 0.03, 0.06), vec3(0.004, 0.01, 0.022), smoothstep(0.0, 1.1, d));
  float halo = exp(-d * d * 5.5) * (0.06 + uEnergy * 0.08 + uLevel * 0.1);
  float neb = snoise(vec3(p * 1.4, uTime * 0.02 * uMotion)) * 0.5 + 0.5;
  vec3 col = base + uColorC * halo * 0.9 + uColorA * halo * neb * 0.25;
  // fine grid, fading out towards the centre and the edges
  vec2 g = abs(fract(p * 9.0) - 0.5);
  float line = smoothstep(0.495, 0.5, max(g.x, g.y));
  col += uColorA * line * 0.025 * smoothstep(0.25, 0.6, d) * smoothstep(1.4, 0.7, d);
  gl_FragColor = vec4(col, 1.0);
}`;
