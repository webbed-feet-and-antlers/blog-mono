import { useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type {
  CompareResponse,
  DiscourseReport,
  DraftMeta,
  FixResponse,
  JobEvent,
  LintReport,
  ReviseResponse,
  SurprisalReport,
  VersionMeta,
} from "./types";

export function useDrafts() {
  return useQuery({
    queryKey: ["drafts"],
    queryFn: () => api<DraftMeta[]>("/api/drafts"),
    refetchInterval: 15_000,
  });
}

export function useVersions(slug: string | null) {
  return useQuery({
    queryKey: ["versions", slug],
    queryFn: () =>
      api<{ versions: VersionMeta[] }>(`/api/drafts/${encodeURIComponent(slug!)}/versions`),
    enabled: !!slug,
  });
}

export function useInvalidateDrafts() {
  const qc = useQueryClient();
  return () => {
    qc.invalidateQueries({ queryKey: ["drafts"] });
    qc.invalidateQueries({ queryKey: ["versions"] });
  };
}

export function useStartDraft() {
  return useMutation({
    mutationFn: (body: { topic: string; research: string; persona: string }) =>
      api<{ job_id: string }>("/api/drafts", { method: "POST", body }),
  });
}

export const runLint = (markdown: string) =>
  api<{ report: LintReport; scorecard: string }>("/api/lint", {
    method: "POST",
    body: { markdown },
  });

export const runScore = (markdown: string) =>
  api<SurprisalReport>("/api/score", { method: "POST", body: { markdown } });

export const runShape = (markdown: string) =>
  api<DiscourseReport>("/api/shape", { method: "POST", body: { markdown } });

export const runCompare = (markdown: string) =>
  api<CompareResponse>("/api/shape/compare", { method: "POST", body: { markdown } });

export const runFix = (markdown: string, blockIndices: number[] | null) =>
  api<FixResponse>("/api/fix", {
    method: "POST",
    body: { markdown, block_indices: blockIndices },
  });

export const runRevise = (body: {
  markdown: string;
  instruction: string;
  selection?: string | null;
}) =>
  api<ReviseResponse>("/api/revise", {
    method: "POST",
    body: { ...body, selection: body.selection ?? null },
  });

/**
 * Subscribes to a job's SSE event stream with automatic polling fallback
 * when EventSource fails. Handlers are kept in refs so they can change
 * without resubscribing.
 */
export function useJobStream(
  jobId: string | null,
  onEvent: (e: JobEvent) => void,
  onDone: (e: JobEvent) => void,
) {
  const eventRef = useRef(onEvent);
  const doneRef = useRef(onDone);
  eventRef.current = onEvent;
  doneRef.current = onDone;

  useEffect(() => {
    if (!jobId) return;
    let closed = false;
    let pollTimer: number | null = null;

    const handle = (raw: string) => {
      const e = JSON.parse(raw) as JobEvent;
      eventRef.current(e);
      if (e.kind === "done") {
        closed = true;
        es?.close();
        if (pollTimer) window.clearInterval(pollTimer);
        doneRef.current(e);
      }
    };

    let es: EventSource | null = null;
    try {
      es = new EventSource(`/api/jobs/${jobId}/events`);
      es.onmessage = (m) => handle(m.data);
      es.onerror = () => {
        // SSE dropped (proxy hiccup, stream already finished) — poll.
        es?.close();
        es = null;
        if (closed || pollTimer) return;
        pollTimer = window.setInterval(async () => {
          try {
            const job = await api<{
              stage: string;
              done: boolean;
              elapsed: number;
              error: string | null;
              result: JobEvent["result"];
              trace: { t: number; event: string }[];
            }>(`/api/jobs/${jobId}`);
            const last = job.trace[job.trace.length - 1];
            handle(
              JSON.stringify({
                kind: job.done ? "done" : "stage",
                stage: job.stage,
                elapsed: job.elapsed,
                event: last?.event,
                error: job.error,
                result: job.result,
              }),
            );
          } catch {
            /* keep polling */
          }
        }, 1500);
      };
    } catch {
      /* EventSource unavailable — let onerror path not apply; poll directly */
    }

    return () => {
      closed = true;
      es?.close();
      if (pollTimer) window.clearInterval(pollTimer);
    };
  }, [jobId]);
}
