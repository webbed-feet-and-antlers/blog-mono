import { Command, History, Moon, PenLine, Sun } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { useEditor } from "@/state/editor";

export function Header({
  onPalette,
  onNewDraft,
  light,
  toggleTheme,
}: {
  onPalette: () => void;
  onNewDraft: () => void;
  light: boolean;
  toggleTheme: () => void;
}) {
  const { slug, setVersionsOpen } = useEditor();
  return (
    <header className="flex items-center justify-between border-b border-line bg-panel px-4 py-2">
      <div className="flex items-center gap-2.5">
        <PenLine size={16} className="text-accent" />
        <h1 className="text-[14px] font-semibold">writing-agent</h1>
        {slug && (
          <span className="hidden font-mono text-[11px] text-muted sm:inline">
            /{slug}
          </span>
        )}
      </div>
      <div className="flex items-center gap-1.5">
        <Button size="sm" variant="ghost" onClick={onPalette} title="Command palette (⌘K)">
          <Command size={12} /> K
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => setVersionsOpen(true)}
          disabled={!slug}
          title="Version history"
        >
          <History size={13} /> <span className="hidden md:inline">History</span>
        </Button>
        <Button
          size="icon"
          variant="ghost"
          onClick={toggleTheme}
          title={light ? "Dark mode" : "Light mode"}
        >
          {light ? <Moon size={13} /> : <Sun size={13} />}
        </Button>
        <Button size="sm" variant="primary" onClick={onNewDraft} className="hidden sm:inline-flex">
          ＋ New draft
        </Button>
      </div>
    </header>
  );
}
