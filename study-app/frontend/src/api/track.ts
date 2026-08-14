/**
 * Activity tracking — in-app actions as agent memory.
 *
 * Buffers interaction events and flushes them to POST /api/activity in
 * batches. Fire-and-forget by design: telemetry must never break the UI.
 *
 * - Regular flushes go through fetch keepalive.
 * - Tab close / hide flushes via navigator.sendBeacon with a text/plain
 *   Blob (no CORS preflight; the backend parses the raw body).
 */

export type ActivityProps = Record<string, string | number | boolean | null | undefined>;

interface BufferedEvent {
  type: string;
  ts: string;
  props: ActivityProps;
}

const FLUSH_INTERVAL_MS = 5_000;
const FLUSH_THRESHOLD = 20;

let buffer: BufferedEvent[] = [];
let flushTimer: ReturnType<typeof setInterval> | null = null;

function ensureTimer(): void {
  if (flushTimer === null && typeof window !== "undefined") {
    flushTimer = setInterval(() => void flush(), FLUSH_INTERVAL_MS);
    // Lifecycle flushes — the beacon is the last reliable chance.
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "hidden") flushWithBeacon();
    });
    window.addEventListener("pagehide", flushWithBeacon);
  }
}

export function track(type: string, props: ActivityProps = {}): void {
  try {
    buffer.push({ type, ts: new Date().toISOString(), props });
    ensureTimer();
    if (buffer.length >= FLUSH_THRESHOLD) void flush();
  } catch {
    // Never let tracking break the caller.
  }
}

export async function flush(): Promise<void> {
  if (buffer.length === 0) return;
  const events = buffer;
  buffer = [];
  try {
    await fetch("/api/activity", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ events }),
      keepalive: true,
    });
  } catch {
    // Dropped telemetry is acceptable; don't retry-storm.
  }
}

function flushWithBeacon(): void {
  if (buffer.length === 0) return;
  const events = buffer;
  buffer = [];
  try {
    // text/plain avoids a CORS preflight on the beacon.
    const blob = new Blob([JSON.stringify({ events })], { type: "text/plain" });
    navigator.sendBeacon("/api/activity", blob);
  } catch {
    // Best effort.
  }
}
