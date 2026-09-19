import { Loader2, Sparkles } from "lucide-react";
import { useState, type FormEvent } from "react";
import { toast } from "sonner";
import { useStartDraft } from "@/api/hooks";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { useJob } from "@/state/job";

export function NewDraftDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [topic, setTopic] = useState("");
  const [research, setResearch] = useState("");
  const start = useStartDraft();
  const { startJob, addChat } = useJob();

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!topic.trim()) return;
    try {
      const { job_id } = await start.mutateAsync({
        topic: topic.trim(),
        research,
        persona: "default",
      });
      addChat("hint", `Drafting “${topic.trim()}” — live trace in the Agent tab.`);
      startJob(job_id, topic.trim());
      setTopic("");
      setResearch("");
      onClose();
    } catch (err) {
      toast.error((err as Error).message);
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={
        <span className="flex items-center gap-2">
          <Sparkles size={14} className="text-accent" /> New draft with the agent
        </span>
      }
    >
      <form onSubmit={submit} className="flex flex-col gap-4">
        <label className="flex flex-col gap-1.5 text-[12px] text-muted">
          Topic
          <input
            autoFocus
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            placeholder="Why our indexer collapsed"
            required
            className="rounded-md border border-line bg-panel2 px-2.5 py-2 text-[13px] text-text outline-none focus:border-accent"
          />
        </label>
        <label className="flex flex-col gap-1.5 text-[12px] text-muted">
          Research notes
          <textarea
            value={research}
            onChange={(e) => setResearch(e.target.value)}
            rows={8}
            placeholder="paste raw notes, numbers, commands, postmortems…"
            className="resize-y rounded-md border border-line bg-panel2 px-2.5 py-2 text-[13px] text-text outline-none focus:border-accent"
          />
        </label>
        <div className="flex items-center justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={start.isPending || !topic.trim()}>
            {start.isPending && <Loader2 size={13} className="animate-spin" />}
            Run agent
          </Button>
        </div>
        <p className="text-[11.5px] leading-5 text-muted">
          Full pipeline: architect blueprint → styled sections → every gate →
          pairwise editor fixes. Typically 3–8 minutes; live progress streams
          into the Agent tab.
        </p>
      </form>
    </Dialog>
  );
}
