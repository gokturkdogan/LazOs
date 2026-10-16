export function formatBytes(bytes: number): string {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1000)));
  const v = bytes / 1000 ** i;
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${units[i]}`;
}

/** RAM is marketed in binary units (24 GB = 24 GiB). */
export function formatMemory(bytes: number): string {
  return `${Math.round(bytes / 1024 ** 3)} GB`;
}

export function formatMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(ms < 10000 ? 2 : 1)} s`;
}

export function formatClock(epochSeconds: number): string {
  return new Date(epochSeconds * 1000).toLocaleTimeString("tr-TR", { hour: "2-digit", minute: "2-digit" });
}

export function formatRelativeDay(epochSeconds: number): string {
  const d = new Date(epochSeconds * 1000);
  const now = new Date();
  const startOf = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((startOf(now) - startOf(d)) / 86400000);
  if (diff === 0) return "Bugün";
  if (diff === 1) return "Dün";
  if (diff < 7) return d.toLocaleDateString("tr-TR", { weekday: "long" });
  return d.toLocaleDateString("tr-TR", { day: "numeric", month: "long", year: "numeric" });
}
