export function Emblem({ size = 20 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden>
      <circle cx="16" cy="16" r="13" stroke="var(--accent)" strokeOpacity="0.45" strokeWidth="1" />
      <circle
        cx="16"
        cy="16"
        r="9.5"
        stroke="var(--accent)"
        strokeWidth="1.6"
        strokeDasharray="10 4"
        className="animate-spin-slow"
        style={{ transformOrigin: "16px 16px", animationDuration: "18s" }}
      />
      <circle cx="16" cy="16" r="4" fill="var(--accent)" />
      <circle cx="16" cy="16" r="6.5" fill="var(--accent)" fillOpacity="0.18" />
    </svg>
  );
}
