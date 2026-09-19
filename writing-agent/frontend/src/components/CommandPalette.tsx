import { CornerDownLeft, Search } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { cn } from "@/lib/utils";

export interface PaletteAction {
  id: string;
  label: string;
  hint?: string;
  run: () => void;
}

export function CommandPalette({
  open,
  onClose,
  actions,
}: {
  open: boolean;
  onClose: () => void;
  actions: PaletteAction[];
}) {
  const [query, setQuery] = useState("");
  const [cursor, setCursor] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return actions;
    return actions.filter((a) => a.label.toLowerCase().includes(q));
  }, [actions, query]);

  useEffect(() => {
    if (open) {
      setQuery("");
      setCursor(0);
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  useEffect(() => setCursor(0), [query]);

  if (!open) return null;

  const runAt = (i: number) => {
    const action = filtered[i];
    if (!action) return;
    onClose();
    action.run();
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 p-4 pt-[12vh]"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="w-full max-w-md overflow-hidden rounded-lg border border-line bg-panel shadow-2xl">
        <div className="flex items-center gap-2 border-b border-line px-3">
          <Search size={14} className="text-muted" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") {
                e.preventDefault();
                setCursor((c) => Math.min(c + 1, filtered.length - 1));
              } else if (e.key === "ArrowUp") {
                e.preventDefault();
                setCursor((c) => Math.max(c - 1, 0));
              } else if (e.key === "Enter") {
                e.preventDefault();
                runAt(cursor);
              } else if (e.key === "Escape") {
                onClose();
              }
            }}
            placeholder="Type a command or draft name…"
            className="h-11 flex-1 bg-transparent text-[13.5px] text-text outline-none placeholder:text-muted"
          />
          <kbd className="rounded border border-line px-1.5 py-0.5 text-[10px] text-muted">esc</kbd>
        </div>
        <div className="max-h-72 overflow-y-auto p-1.5">
          {filtered.map((a, i) => (
            <button
              key={a.id}
              onClick={() => runAt(i)}
              onMouseEnter={() => setCursor(i)}
              className={cn(
                "flex w-full items-center justify-between rounded-md px-2.5 py-2 text-left text-[13px]",
                i === cursor ? "bg-accent/15 text-text" : "text-muted",
              )}
            >
              <span className="truncate">{a.label}</span>
              <span className="flex items-center gap-2 text-[11px] text-muted">
                {a.hint}
                {i === cursor && <CornerDownLeft size={11} />}
              </span>
            </button>
          ))}
          {filtered.length === 0 && (
            <p className="px-3 py-6 text-center text-[12.5px] text-muted">No matches.</p>
          )}
        </div>
      </div>
    </div>
  );
}
