import { cn } from "@/lib/utils";

export interface TabDef {
  id: string;
  label: string;
  count?: number;
}

export function Tabs({
  tabs,
  active,
  onChange,
}: {
  tabs: TabDef[];
  active: string;
  onChange: (id: string) => void;
}) {
  return (
    <div className="flex items-center gap-1 border-b border-line px-2">
      {tabs.map((t) => (
        <button
          key={t.id}
          onClick={() => onChange(t.id)}
          className={cn(
            "relative -mb-px border-b-2 px-3 py-2 text-[12.5px] font-medium transition-colors",
            active === t.id
              ? "border-accent text-text"
              : "border-transparent text-muted hover:text-text",
          )}
        >
          {t.label}
          {t.count != null && t.count > 0 && (
            <span className="ml-1.5 rounded-full bg-err/15 px-1.5 py-px text-[10px] text-err">
              {t.count}
            </span>
          )}
        </button>
      ))}
    </div>
  );
}
