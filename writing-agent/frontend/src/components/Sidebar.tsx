import { FileText, Plus } from "lucide-react";
import { useState } from "react";
import { NewDraftDialog } from "@/components/NewDraftDialog";
import { Button } from "@/components/ui/Button";
import { useDrafts } from "@/api/hooks";
import { useEditor } from "@/state/editor";
import { cn } from "@/lib/utils";

export function Sidebar() {
  const { data: drafts } = useDrafts();
  const { slug, openDraft, newBlankDraft } = useEditor();
  const [newOpen, setNewOpen] = useState(false);

  return (
    <div className="flex h-full flex-col gap-3 overflow-y-auto p-3">
      <Button variant="primary" onClick={() => setNewOpen(true)}>
        <Plus size={14} /> New draft with agent
      </Button>
      <Button variant="ghost" onClick={newBlankDraft} className="justify-start">
        <Plus size={13} /> Blank draft
      </Button>

      <div className="mt-2 text-[11px] font-semibold uppercase tracking-wider text-muted">
        Drafts
      </div>
      <ul className="flex flex-col gap-0.5">
        {(drafts ?? []).map((d) => (
          <li key={d.slug}>
            <button
              onClick={() => void openDraft(d.slug)}
              className={cn(
                "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px] transition-colors",
                d.slug === slug
                  ? "bg-panel2 text-text"
                  : "text-muted hover:bg-panel2/60 hover:text-text",
              )}
            >
              <FileText size={13} className="shrink-0" />
              <span className="truncate">{d.slug}</span>
            </button>
          </li>
        ))}
        {drafts?.length === 0 && (
          <li className="px-2 py-1 text-[12px] text-muted">No drafts yet.</li>
        )}
      </ul>

      <NewDraftDialog open={newOpen} onClose={() => setNewOpen(false)} />
    </div>
  );
}
