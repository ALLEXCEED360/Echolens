/** Display formatting.
 *
 * Timestamps are formatted here and only here. Everything upstream — database,
 * API, application state — carries float seconds, per docs/01-data-model.md.
 */

/** Seconds → `MM:SS`, or `HH:MM:SS` once the hour mark is passed. */
export function formatTimestamp(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return "--:--";

  const total = Math.floor(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (n: number) => String(n).padStart(2, "0");

  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}

/** Parse `HH:MM:SS`, `MM:SS`, or a bare seconds count. Returns null if unparseable. */
export function parseTimestamp(input: string): number | null {
  const trimmed = input.trim();
  if (!trimmed) return null;

  const parts = trimmed.split(":");
  if (parts.some((p) => p === "" || !/^\d+(\.\d+)?$/.test(p))) return null;

  const nums = parts.map(Number);
  if (nums.some(Number.isNaN)) return null;

  switch (nums.length) {
    case 1:
      return nums[0];
    case 2:
      return nums[0] * 60 + nums[1];
    case 3:
      return nums[0] * 3600 + nums[1] * 60 + nums[2];
    default:
      return null;
  }
}

export function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / 1024 ** i;
  return `${value.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

export function formatResolution(width: number | null, height: number | null): string {
  return width && height ? `${width} × ${height}` : "—";
}

export function formatRelativeDate(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";

  const seconds = Math.floor((Date.now() - then) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  if (seconds < 604800) return `${Math.floor(seconds / 86400)}d ago`;
  return new Date(iso).toLocaleDateString();
}
