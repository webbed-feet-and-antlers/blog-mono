import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ChevronDown,
  ChevronRight,
  FolderOpen,
  Layers,
  SkipForward,
} from "lucide-react";
import * as api from "../api/client";
import { track } from "../api/track";
import { groupModulesBySemester } from "../lib/semesters";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";

export interface FilingTarget {
  moduleId?: string;
  lessonId?: string;
}

interface Props {
  /** What's being filed — shapes the copy ("document" | "lecture"). */
  noun: "document" | "lecture" | "documents";
  onSelect: (target: FilingTarget | null) => void;
  /** "skip" button label override (defaults to "Skip for now"). */
  skipLabel?: string;
}

/**
 * "Add to module" prompt — shown when a document or lecture would land
 * unfiled. Current-semester modules first and expanded; older semesters
 * collapsed but one click away. Selecting a module files to its root;
 * expanding it offers its lessons.
 */
export function FileToModuleModal({ noun, onSelect, skipLabel }: Props) {
  const tree = useQuery({
    queryKey: ["module-tree"],
    queryFn: api.listModuleTree,
  });
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  const modules = tree.data?.modules ?? [];
  const groups = groupModulesBySemester(modules);

  function select(target: FilingTarget | null, semesterLabel: string | null) {
    track("filing.selected", {
      surface: noun,
      has_module: !!target?.moduleId,
      has_lesson: !!target?.lessonId,
      semester: semesterLabel,
    });
    onSelect(target);
  }

  return (
    <Dialog open onOpenChange={(open) => !open && select(null, null)}>
      <DialogContent className="file-to-module-modal flex max-h-[min(80vh,640px)] flex-col gap-0 overflow-hidden p-0 sm:max-w-md">
        <DialogTitle className="sr-only">
          Add {noun === "lecture" ? "lecture" : noun} to a module?
        </DialogTitle>
        <DialogDescription className="sr-only">
          Current semester first — you can move it later
        </DialogDescription>
        <div className="understanding-header">
          <div className="understanding-header-icon">
            <FolderOpen size={18} />
          </div>
          <div className="understanding-header-text">
            <h3>Add {noun === "lecture" ? "lecture" : noun} to a module?</h3>
            <p>current semester first — you can move it later</p>
          </div>
        </div>

        <div className="understanding-body">
          {tree.isLoading && (
            <div className="concept-detail-none">Loading modules…</div>
          )}
          {!tree.isLoading && modules.length === 0 && (
            <div className="concept-detail-none">
              No modules yet — it will stay unfiled for now.
            </div>
          )}
          <div className="semester-groups">
            {groups.map((g) => {
              const isOpen =
                g.isCurrent || g.isUnsorted
                  ? expanded[g.key] !== false
                  : expanded[g.key] === true;
              return (
                <Collapsible
                  key={g.key}
                  open={isOpen}
                  onOpenChange={(open) =>
                    setExpanded((prev) => ({ ...prev, [g.key]: open }))
                  }
                  className="semester-group"
                >
                  <CollapsibleTrigger
                    className={`semester-group-header ${g.isCurrent ? "current" : ""}`}
                  >
                    <ChevronDown
                      size={14}
                      className="semester-group-chevron transition-transform group-data-[state=closed]/collapsible:-rotate-90"
                    />
                    <span className="semester-group-label">{g.label}</span>
                    {g.isCurrent && (
                      <span className="semester-group-tag">This semester</span>
                    )}
                    <span className="semester-group-count">
                      {g.modules.length} module{g.modules.length === 1 ? "" : "s"}
                    </span>
                  </CollapsibleTrigger>
                  <CollapsibleContent>
                    <div className="file-to-module-list">
                      {g.modules.map((m) => {
                        const modOpen = expanded[`m-${m.id}`] === true;
                        return (
                          <Collapsible
                            key={m.id}
                            open={modOpen}
                            onOpenChange={(open) =>
                              setExpanded((prev) => ({
                                ...prev,
                                [`m-${m.id}`]: open,
                              }))
                            }
                            className="file-to-module-module"
                          >
                            <button
                              type="button"
                              className="file-to-module-row"
                              onClick={() =>
                                select({ moduleId: m.id }, g.isUnsorted ? null : g.label)
                              }
                            >
                              <FolderOpen size={15} className="ftm-icon" />
                              <span className="ftm-name">{m.title}</span>
                              {m.lessons.length > 0 && (
                                <CollapsibleTrigger asChild>
                                  <span
                                    className="ftm-expand"
                                    onClick={(e) => e.stopPropagation()}
                                    title="Choose a lesson instead"
                                  >
                                    {m.lessons.length} lesson
                                    {m.lessons.length === 1 ? "" : "s"}
                                    {modOpen ? (
                                      <ChevronDown size={13} />
                                    ) : (
                                      <ChevronRight size={13} />
                                    )}
                                  </span>
                                </CollapsibleTrigger>
                              )}
                            </button>
                            <CollapsibleContent>
                              <div className="file-to-module-lessons">
                                {m.lessons.map((l) => (
                                  <button
                                    key={l.id}
                                    type="button"
                                    className="file-to-module-row lesson"
                                    onClick={() =>
                                      select(
                                        { moduleId: m.id, lessonId: l.id },
                                        g.isUnsorted ? null : g.label,
                                      )
                                    }
                                  >
                                    <Layers size={14} className="ftm-icon" />
                                    <span className="ftm-name">{l.title}</span>
                                  </button>
                                ))}
                              </div>
                            </CollapsibleContent>
                          </Collapsible>
                        );
                      })}
                    </div>
                  </CollapsibleContent>
                </Collapsible>
              );
            })}
          </div>
        </div>

        <div className="file-to-module-footer">
          <Button variant="ghost" onClick={() => select(null, null)}>
            <SkipForward size={14} />
            {skipLabel ?? "Skip for now"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
