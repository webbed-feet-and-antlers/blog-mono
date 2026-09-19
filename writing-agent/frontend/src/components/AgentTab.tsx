import { ArrowUp, Loader2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { runRevise, useJobStream } from "@/api/hooks";
import { Button } from "@/components/ui/Button";
import { useEditor } from "@/state/editor";
import { useJob } from "@/state/job";
import { cn } from "@/lib/utils";

export function AgentTab() {
  const {
    jobId, topic, stage, events, running, doneEvent, chat, pushEvent, finishJob, addChat,
  } = useJob();
  const { markdown, selection, openDraft, proposeChange } = useEditor();
  const [instruction, setInstruction] = useState("");
  const [sending, setSending] = useState(false);
  const traceRef = useRef<HTMLDivElement>(null);
  const chatRef = useRef<HTMLDivElement>(null);
  const [tick, setTick] = useState(0);

  useJobStream(
    jobId,
    (e) => pushEvent(e),
    (e) => {
      finishJob(e);
      if (e.error) {
        addChat("error", `Agent failed: ${e.error}`);
        toast.error(e.error);
        return;
      }
      if (e.result) {
        void openDraft(e.result.slug);
        addChat(
          "agent",
          `Drafted “${topic}”. Gates ran in the pipeline — your turn: edit freely, or send instructions below.`,
        );
        toast.success(`Draft ready: ${e.result.slug}`);
      }
    },
  );

  // Live seconds counter while running.
  useEffect(() => {
    if (!running) return;
    const t = window.setInterval(() => setTick((n) => n + 1), 1000);
    return () => window.clearInterval(t);
  }, [running]);

  useEffect(() => {
    traceRef.current?.scrollTo({ top: traceRef.current.scrollHeight });
  }, [events.length]);
  useEffect(() => {
    chatRef.current?.scrollTo({ top: chatRef.current.scrollHeight });
  }, [chat.length]);

  const send = async () => {
    const text = instruction.trim();
    if (!text || sending) return;
    setSending(true);
    setInstruction("");
    const scoped = selection && selection.trim().length > 3;
    addChat("you", `${text}${scoped ? "  [selection]" : ""}`);
    try {
      const res = await runRevise({
        markdown,
        instruction: text,
        selection: scoped ? selection : null,
      });
      proposeChange({
        title: `Revision (${res.scope})`,
        before: markdown,
        after: res.markdown,
        source: "agent",
      });
      addChat(
        "agent",
        `Proposed a ${res.scope}-scoped revision — review the diff, accept or reject.`,
      );
    } catch (err) {
      addChat("error", (err as Error).message);
    } finally {
      setSending(false);
    }
  };

  const lastElapsed = events.length ? events[events.length - 1].elapsed : 0;
  void tick; // re-render each second while running

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 p-3">
      {running && (
        <div className="flex items-center gap-2 rounded-md border border-accent/30 bg-accent/5 px-3 py-2 text-[12.5px] text-accent">
          <Loader2 size={13} className="animate-spin" />
          <span className="flex-1 truncate">{stage ?? "Starting…"}</span>
          <span className="font-mono text-[11px] text-muted">{Math.round(lastElapsed)}s</span>
        </div>
      )}
      {doneEvent?.error && (
        <div className="rounded-md border border-err/40 bg-err/5 px-3 py-2 text-[12px] text-err">
          {doneEvent.error}
        </div>
      )}

      <div
        ref={traceRef}
        className={cn(
          "min-h-0 flex-1 overflow-y-auto rounded-md border border-line bg-panel2 p-2 font-mono text-[11px] leading-5",
          events.length === 0 && "hidden",
        )}
      >
        {events
          .filter((e) => e.event || e.kind === "stage")
          .map((e, i) => (
            <div key={i} className="flex gap-2">
              <span className="w-12 shrink-0 text-right text-muted">
                {Math.round(e.elapsed)}s
              </span>
              <span
                className={cn(
                  e.kind === "done" && e.error && "text-err",
                  e.kind === "done" && !e.error && "text-ok",
                )}
              >
                {e.event ?? `▸ ${e.stage}`}
              </span>
            </div>
          ))}
      </div>

      <div ref={chatRef} className="max-h-56 min-h-0 shrink-0 overflow-y-auto">
        {chat.map((c, i) => (
          <p
            key={i}
            className={cn(
              "mb-1.5 rounded-md px-2.5 py-1.5 text-[12.5px] leading-5",
              c.role === "you" && "bg-panel2 text-text",
              c.role === "agent" && "border border-accent/25 bg-accent/5 text-text",
              c.role === "error" && "text-err",
              c.role === "hint" && "text-muted",
            )}
          >
            {c.text}
          </p>
        ))}
      </div>

      <div className="flex shrink-0 flex-col gap-2 rounded-md border border-line bg-panel2 p-2">
        <textarea
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
          onKeyDown={(e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
              e.preventDefault();
              void send();
            }
          }}
          rows={3}
          placeholder="e.g. make the intro punchier; add a failing command example; this reads like marketing, cut it down"
          className="resize-none rounded-md border border-line bg-bg px-2.5 py-2 text-[13px] text-text outline-none focus:border-accent"
        />
        <div className="flex items-center justify-between">
          <span className="text-[11px] text-muted">
            {selection && selection.trim().length > 3
              ? `scope: selected passage (${selection.trim().split(/\s+/).length} words)`
              : "scope: whole document — select text to narrow"}
          </span>
          <Button variant="primary" size="sm" onClick={() => void send()} disabled={sending || !instruction.trim()}>
            {sending ? <Loader2 size={13} className="animate-spin" /> : <ArrowUp size={13} />}
            Send ⌘⏎
          </Button>
        </div>
      </div>
    </div>
  );
}
