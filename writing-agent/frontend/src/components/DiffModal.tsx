import { Check, X } from "lucide-react";
import { useMemo } from "react";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { collapsedRows, lineDiff } from "@/lib/diff";
import { useEditor } from "@/state/editor";
import { cn } from "@/lib/utils";

export function DiffModal() {
  const { diff, acceptChange, rejectChange } = useEditor();
  const rows = useMemo(
    () => (diff ? collapsedRows(lineDiff(diff.before, diff.after)) : []),
    [diff],
  );

  if (!diff) return null;
  return (
    <Dialog
      open
      onClose={rejectChange}
      wide
      title={`Review change — ${diff.title}`}
      footer={
        <>
          <Button variant="ghost" onClick={rejectChange}>
            <X size={13} /> Reject
          </Button>
          <Button variant="primary" onClick={() => void acceptChange()}>
            <Check size={13} /> Accept
          </Button>
        </>
      }
    >
      <div className="rounded-md border border-line bg-panel2 font-mono text-[12px] leading-6">
        {rows.map((row, i) => {
          if (row.type === "gap") {
            return (
              <div key={i} className="px-3 py-0.5 text-center text-[10.5px] text-muted">
                ⋯ {row.count} unchanged line{row.count === 1 ? "" : "s"} ⋯
              </div>
            );
          }
          if (row.type === "same") {
            return (
              <div key={i} className="whitespace-pre-wrap px-3 text-muted">
                {row.text || "\u00A0"}
              </div>
            );
          }
          if (row.type === "mod") {
            return (
              <div key={i} className="whitespace-pre-wrap border-l-2 border-warn px-3">
                {row.parts.map((p, j) => (
                  <span
                    key={j}
                    className={cn(
                      p.type === "same" && "text-text",
                      p.type === "del" && "rounded bg-err/15 text-err line-through",
                      p.type === "add" && "rounded bg-ok/15 text-ok",
                    )}
                  >
                    {p.text}
                  </span>
                ))}
              </div>
            );
          }
          return (
            <div
              key={i}
              className={cn(
                "whitespace-pre-wrap border-l-2 px-3",
                row.type === "del" ? "border-err bg-err/5 text-err" : "border-ok bg-ok/5 text-ok",
              )}
            >
              {row.type === "del" ? "− " : "+ "}
              {row.text}
            </div>
          );
        })}
      </div>
      <p className="mt-2 text-[11px] text-muted">
        Accepting snapshots the current version to history first — nothing is
        ever lost.
      </p>
    </Dialog>
  );
}
