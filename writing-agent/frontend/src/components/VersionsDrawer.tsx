import { History, Loader2, RotateCcw } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { api } from "@/api/client";
import { useVersions } from "@/api/hooks";
import type { VersionMeta } from "@/api/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { collapsedRows, lineDiff } from "@/lib/diff";
import { useEditor } from "@/state/editor";
import { cn } from "@/lib/utils";

const SOURCE_TONE: Record<string, "accent" | "ok" | "warn" | "neutral"> = {
  human: "neutral",
  agent: "accent",
  fix: "accent",
  pipeline: "ok",
  revert: "warn",
};

export function VersionsDrawer() {
  const { slug, markdown, versionsOpen, setVersionsOpen, openDraft } = useEditor();
  const { data, isLoading } = useVersions(versionsOpen ? slug : null);
  const [selected, setSelected] = useState<VersionMeta | null>(null);
  const [content, setContent] = useState<string | null>(null);
  const [restoring, setRestoring] = useState(false);

  const pick = async (v: VersionMeta) => {
    setSelected(v);
    setContent(null);
    try {
      const res = await api<{ markdown: string }>(
        `/api/drafts/${encodeURIComponent(slug!)}/versions/${encodeURIComponent(v.file)}`,
      );
      setContent(res.markdown);
    } catch (err) {
      toast.error((err as Error).message);
    }
  };

  const restore = async () => {
    if (!selected) return;
    setRestoring(true);
    try {
      await api(`/api/drafts/${encodeURIComponent(slug!)}/revert`, {
        method: "POST",
        body: { file: selected.file },
      });
      await openDraft(slug!);
      toast.success(`Restored ${selected.file}`);
      setSelected(null);
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setRestoring(false);
    }
  };

  const rows =
    selected && content !== null
      ? collapsedRows(lineDiff(content, markdown))
      : [];

  return (
    <Dialog
      open={versionsOpen && !!slug}
      onClose={() => setVersionsOpen(false)}
      wide
      title={
        <span className="flex items-center gap-2">
          <History size={14} /> Version history — {slug}
        </span>
      }
      footer={
        selected && (
          <>
            <Button variant="ghost" onClick={() => setSelected(null)}>
              Back to list
            </Button>
            <Button variant="primary" onClick={() => void restore()} disabled={restoring}>
              {restoring ? <Loader2 size={13} className="animate-spin" /> : <RotateCcw size={13} />}
              Restore this version
            </Button>
          </>
        )
      }
    >
      {!selected ? (
        <div className="flex flex-col gap-1">
          {isLoading && (
            <div className="flex justify-center py-8">
              <Loader2 className="animate-spin text-muted" size={18} />
            </div>
          )}
          {(data?.versions ?? []).map((v) => (
            <button
              key={v.file}
              onClick={() => void pick(v)}
              className="flex items-center justify-between rounded-md px-2.5 py-2 text-left text-[12.5px] hover:bg-panel2"
            >
              <span className="font-mono text-muted">{v.file}</span>
              <Badge tone={SOURCE_TONE[v.source] ?? "neutral"}>{v.source}</Badge>
            </button>
          ))}
          {!isLoading && (data?.versions.length ?? 0) === 0 && (
            <p className="py-6 text-center text-[12.5px] text-muted">
              No versions yet — every save and AI change lands here.
            </p>
          )}
        </div>
      ) : (
        <div className="rounded-md border border-line bg-panel2 font-mono text-[12px] leading-6">
          {content === null ? (
            <div className="flex justify-center py-8">
              <Loader2 className="animate-spin text-muted" size={18} />
            </div>
          ) : (
            rows.map((row, i) => {
              if (row.type === "gap") {
                return (
                  <div key={i} className="px-3 py-0.5 text-center text-[10.5px] text-muted">
                    ⋯ {row.count} unchanged ⋯
                  </div>
                );
              }
              return (
                <div
                  key={i}
                  className={cn(
                    "whitespace-pre-wrap px-3",
                    row.type === "same" && "text-muted",
                    row.type === "del" && "bg-ok/10 text-ok", // version had it, current doesn't
                    row.type === "add" && "bg-err/10 text-err",
                    row.type === "mod" && "border-l-2 border-warn",
                  )}
                >
                  {row.type === "mod" ? `${row.a} → ${row.b}` : row.text || "\u00A0"}
                </div>
              );
            })
          )}
        </div>
      )}
    </Dialog>
  );
}
