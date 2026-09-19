import { Eye, Columns2, Pencil, Save } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import Markdown from "react-markdown";
import { CodeMirror } from "@/components/CodeMirror";
import { Button } from "@/components/ui/Button";
import { useEditor } from "@/state/editor";
import { cn } from "@/lib/utils";

type Mode = "edit" | "split" | "preview";

export function EditorPane() {
  const { slug, markdown, dirty, setContent, save, setSelection, savedAt } = useEditor();
  const [mode, setMode] = useState<Mode>("split");
  const saveRef = useRef(save);
  saveRef.current = save;

  // Debounced autosave: 2.5s after the last keystroke, when dirty.
  useEffect(() => {
    if (!dirty || !markdown.trim()) return;
    const timer = window.setTimeout(() => void saveRef.current("human"), 2500);
    return () => window.clearTimeout(timer);
  }, [markdown, dirty]);

  // ⌘S / Ctrl+S saves.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        void saveRef.current("human");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const savedLabel = savedAt
    ? dirty
      ? "unsaved changes…"
      : "saved"
    : "not saved yet";

  return (
    <div className="flex h-full min-w-0 flex-col">
      <div className="flex items-center justify-between border-b border-line px-3 py-1.5">
        <div className="flex items-center gap-2 text-[12px] text-muted">
          <span className="max-w-56 truncate font-mono text-text">
            {slug ?? "untitled draft"}
          </span>
          <span
            className={cn(
              "h-1.5 w-1.5 rounded-full",
              dirty ? "bg-warn" : "bg-ok",
            )}
            title={savedLabel}
          />
          <span className="hidden xl:inline">{savedLabel}</span>
        </div>
        <div className="flex items-center gap-1">
          {(["edit", "split", "preview"] as Mode[]).map((m) => (
            <Button
              key={m}
              size="icon"
              variant={mode === m ? "primary" : "ghost"}
              onClick={() => setMode(m)}
              title={
                m === "edit"
                  ? "Source only"
                  : m === "split"
                    ? "Source + preview"
                    : "Preview only"
              }
            >
              {m === "edit" ? (
                <Pencil size={13} />
              ) : m === "split" ? (
                <Columns2 size={13} />
              ) : (
                <Eye size={13} />
              )}
            </Button>
          ))}
          <Button size="sm" variant="ghost" onClick={() => void save("human")} title="Save (⌘S)">
            <Save size={13} /> Save
          </Button>
        </div>
      </div>
      <div className="flex min-h-0 flex-1">
        {mode !== "preview" && (
          <div className={cn("min-w-0 flex-1 overflow-auto p-1", mode === "split" && "border-r border-line")}>
            {markdown === "" && mode === "edit" ? (
              <EmptyEditor />
            ) : (
              <CodeMirror value={markdown} onChange={setContent} onSelection={setSelection} />
            )}
          </div>
        )}
        {mode !== "edit" && (
          <div className="min-w-0 flex-1 overflow-auto px-6 py-4">
            <div className="prose-preview mx-auto max-w-[46rem]">
              <Markdown>{markdown || "*Nothing yet — start typing or draft with the agent.*"}</Markdown>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function EmptyEditor() {
  return (
    <div className="flex h-full items-center justify-center text-[13px] text-muted">
      <div className="max-w-sm text-center leading-6">
        Empty draft. Type to begin, or use{" "}
        <span className="font-semibold text-text">＋ New draft</span> in the
        sidebar to run the agent. <kbd className="rounded border border-line px-1">⌘K</kbd>{" "}
        for the command palette.
      </div>
    </div>
  );
}
