import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";
import { useEffect, useState } from "react";
import { AgentTab } from "@/components/AgentTab";
import { CommandPalette, type PaletteAction } from "@/components/CommandPalette";
import { DiffModal } from "@/components/DiffModal";
import { EditorPane } from "@/components/EditorPane";
import { GatesTab } from "@/components/GatesTab";
import { Header } from "@/components/Header";
import { NewDraftDialog } from "@/components/NewDraftDialog";
import { ShapeTab } from "@/components/ShapeTab";
import { Sidebar } from "@/components/Sidebar";
import { VersionsDrawer } from "@/components/VersionsDrawer";
import { Tabs } from "@/components/ui/Tabs";
import { useDrafts } from "@/api/hooks";
import { useEditor } from "@/state/editor";
import { useTheme } from "@/lib/useTheme";

const RIGHT_TABS = [
  { id: "gates", label: "Gates" },
  { id: "shape", label: "Shape" },
  { id: "agent", label: "Agent" },
];

export function App() {
  const { light, toggle } = useTheme();
  const [tab, setTab] = useState("gates");
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [newDraftOpen, setNewDraftOpen] = useState(false);
  const { data: drafts } = useDrafts();
  const { openDraft, setVersionsOpen, newBlankDraft } = useEditor();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((o) => !o);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const actions: PaletteAction[] = [
    { id: "new", label: "New draft with agent", hint: "agent", run: () => setNewDraftOpen(true) },
    { id: "blank", label: "New blank draft", run: newBlankDraft },
    { id: "gates", label: "Run gates (Lint)", hint: "Gates tab", run: () => setTab("gates") },
    { id: "shape", label: "Run shape analysis", hint: "Shape tab", run: () => setTab("shape") },
    { id: "agent", label: "Co-editor chat", hint: "Agent tab", run: () => setTab("agent") },
    ...(slugActions(drafts ?? [], openDraft)),
    { id: "history", label: "Version history", run: () => setVersionsOpen(true) },
    { id: "theme", label: light ? "Switch to dark mode" : "Switch to light mode", run: toggle },
  ];

  return (
    <div className="flex h-full flex-col">
      <Header
        onPalette={() => setPaletteOpen(true)}
        onNewDraft={() => setNewDraftOpen(true)}
        light={light}
        toggleTheme={toggle}
      />
      <div className="min-h-0 flex-1">
        <PanelGroup direction="horizontal" autoSaveId="wa-layout">
          <Panel defaultSize={19} minSize={14} className="hidden bg-panel lg:block">
            <Sidebar />
          </Panel>
          <PanelResizeHandle className="hidden w-px bg-line transition-colors hover:bg-accent lg:block" />
          <Panel defaultSize={48} minSize={25} className="bg-bg">
            <EditorPane />
          </Panel>
          <PanelResizeHandle className="w-px bg-line transition-colors hover:bg-accent" />
          <Panel defaultSize={33} minSize={24} className="bg-panel">
            <div className="flex h-full flex-col">
              <Tabs tabs={RIGHT_TABS} active={tab} onChange={setTab} />
              <div className="min-h-0 flex-1">
                {tab === "gates" && <GatesTab />}
                {tab === "shape" && <ShapeTab />}
                {tab === "agent" && <AgentTab />}
              </div>
            </div>
          </Panel>
        </PanelGroup>
      </div>

      <NewDraftDialog open={newDraftOpen} onClose={() => setNewDraftOpen(false)} />
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} actions={actions} />
      <DiffModal />
      <VersionsDrawer />
    </div>
  );
}

function slugActions(
  drafts: { slug: string }[],
  openDraft: (slug: string) => Promise<void>,
): PaletteAction[] {
  return drafts.slice(0, 15).map((d) => ({
    id: `open-${d.slug}`,
    label: `Open ${d.slug}`,
    hint: "draft",
    run: () => void openDraft(d.slug),
  }));
}
