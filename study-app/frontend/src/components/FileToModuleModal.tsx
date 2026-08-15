import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  X,
  ChevronDown,
  ChevronRight,
  FolderOpen,
  Layers,
  SkipForward,
} from "lucide-react";
import * as api from "../api/client";
import { track } from "../api/track";
import { groupModulesBySemester } from "../lib/semesters";

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
    <div className="modal-backdrop" onClick={() => select(null, null)}>
      <div
        className="modal-content file-to-module-modal"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="understanding-header">
          <div className="understanding-header-icon">
            <FolderOpen size={18} />
          </div>
          <div className="understanding-header-text">
            <h3>Add {noun === "lecture" ? "lecture" : noun} to a module?</h3>
            <p>current semester first — you can move it later</p>
          </div>
          <button
            type="button"
            className="ghost icon-btn"
            onClick={() => select(null, null)}
            aria-label="Close"
          >
            <X size={16} />
          </button>
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
                <section key={g.key} className="semester-group">
                  <button
                    type="button"
                    className={`semester-group-header ${g.isCurrent ? "current" : ""}`}
                    onClick={() => setExpanded((prev) => ({ ...prev, [g.key]: !isOpen }))}
                  >
                    <ChevronDown
                      size={14}
                      className="semester-group-chevron"
                      style={{ transform: isOpen ? "" : "rotate(-90deg)" }}
                    />
                    <span className="semester-group-label">{g.label}</span>
                    {g.isCurrent && (
                      <span className="semester-group-tag">This semester</span>
                    )}
                    <span className="semester-group-count">
                      {g.modules.length} module{g.modules.length === 1 ? "" : "s"}
                    </span>
                  </button>
                  {isOpen && (
                    <div className="file-to-module-list">
                      {g.modules.map((m) => {
                        const modOpen = expanded[`m-${m.id}`] === true;
                        return (
                          <div key={m.id} className="file-to-module-module">
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
                                <span
                                  role="button"
                                  tabIndex={0}
                                  className="ftm-expand"
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    setExpanded((prev) => ({
                                      ...prev,
                                      [`m-${m.id}`]: !modOpen,
                                    }));
                                  }}
                                  onKeyDown={(e) => {
                                    if (e.key === "Enter" || e.key === " ") {
                                      e.stopPropagation();
                                      setExpanded((prev) => ({
                                        ...prev,
                                        [`m-${m.id}`]: !modOpen,
                                      }));
                                    }
                                  }}
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
                              )}
                            </button>
                            {modOpen && (
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
                            )}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </section>
              );
            })}
          </div>
        </div>

        <div className="file-to-module-footer">
          <button
            type="button"
            className="ghost"
            onClick={() => select(null, null)}
          >
            <SkipForward size={14} />
            {skipLabel ?? "Skip for now"}
          </button>
        </div>
      </div>
    </div>
  );
}
