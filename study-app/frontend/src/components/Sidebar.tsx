import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BookOpen,
  FileText,
  UploadCloud,
  X,
  Loader2,
  Plus,
  ChevronRight,
  FolderOpen,
  Folder,
  FolderInput,
  Mic,
  Square,
} from "lucide-react";
import * as api from "../api/client";
import type { ModuleTree } from "../types";
import { ProfileCard } from "./ProfileCard";

interface Props {
  selectedId: string | null;
  onNavigate: (id: string) => void;
  onHome: () => void;
}

export function Sidebar({ selectedId, onNavigate, onHome }: Props) {
  const queryClient = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(() => {
    // Persist expanded state in localStorage.
    try {
      const saved = localStorage.getItem("sidebar-expanded");
      return saved ? new Set(JSON.parse(saved)) : new Set();
    } catch {
      return new Set();
    }
  });
  const [addingToModule, setAddingToModule] = useState<string | null>(null);
  const [newModuleName, setNewModuleName] = useState("");
  const [showNewModule, setShowNewModule] = useState(false);
  // Recording state
  const [isRecording, setIsRecording] = useState(false);
  const [recordTime, setRecordTime] = useState(0);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const recordTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const tree = useQuery({
    queryKey: ["module-tree"],
    queryFn: api.listModuleTree,
  });

  const upload = useMutation({
    mutationFn: (file: File) =>
      api.uploadDocument(file, undefined, (pct) => setUploadProgress(pct)),
    onSuccess: (doc) => {
      setUploadProgress(null);
      queryClient.invalidateQueries({ queryKey: ["documents"] });
      queryClient.invalidateQueries({ queryKey: ["module-tree"] });
      onNavigate(doc.id);
    },
    onError: () => setUploadProgress(null),
  });

  const remove = useMutation({
    mutationFn: api.deleteDocument,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["documents"] });
      queryClient.invalidateQueries({ queryKey: ["module-tree"] });
    },
  });

  const createModule = useMutation({
    mutationFn: (title: string) => api.createModule(title),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["module-tree"] });
      setNewModuleName("");
      setShowNewModule(false);
    },
  });

  const createLesson = useMutation({
    mutationFn: ({ moduleId, title }: { moduleId: string; title: string }) =>
      api.createLesson(moduleId, title),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["module-tree"] });
      setAddingToModule(null);
    },
  });

  const deleteModule = useMutation({
    mutationFn: api.deleteModule,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["module-tree"] }),
  });

  const deleteLesson = useMutation({
    mutationFn: api.deleteLesson,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["module-tree"] }),
  });

  function toggle(id: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      try {
        localStorage.setItem("sidebar-expanded", JSON.stringify([...next]));
      } catch {
        /* ignore */
      }
      return next;
    });
  }

  async function handleFiles(files: FileList | null) {
    if (!files || files.length === 0) return;
    await upload.mutateAsync(files[0]);
    if (fileInput.current) fileInput.current.value = "";
  }

  // --- In-browser audio recording (MediaRecorder API) ---
  async function toggleRecording() {
    if (isRecording) {
      stopRecording();
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mimeType = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
        ? "audio/webm;codecs=opus"
        : "audio/mp4";
      const recorder = new MediaRecorder(stream, { mimeType });
      mediaRecorderRef.current = recorder;
      chunksRef.current = [];

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };

      recorder.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        const blob = new Blob(chunksRef.current, { type: mimeType });
        const ext = mimeType.includes("webm") ? "webm" : "m4a";
        const file = new File([blob], `recording-${Date.now()}.${ext}`, {
          type: mimeType,
        });
        await upload.mutateAsync(file);
      };

      recorder.start();
      setIsRecording(true);
      setRecordTime(0);
      recordTimerRef.current = setInterval(() => {
        setRecordTime((t) => t + 1);
      }, 1000);
    } catch (err) {
      console.error("Recording failed:", err);
    }
  }

  function stopRecording() {
    if (mediaRecorderRef.current && isRecording) {
      mediaRecorderRef.current.stop();
      setIsRecording(false);
      if (recordTimerRef.current) clearInterval(recordTimerRef.current);
    }
  }

  // Cleanup on unmount.
  useEffect(() => {
    return () => {
      if (recordTimerRef.current) clearInterval(recordTimerRef.current);
    };
  }, []);

  function formatTime(secs: number): string {
    const m = Math.floor(secs / 60);
    const s = secs % 60;
    return `${m}:${s.toString().padStart(2, "0")}`;
  }

  const allDocs = [
    ...(tree.data?.modules.flatMap((m) =>
      m.lessons.flatMap((l) => l.documents),
    ) ?? []),
    ...(tree.data?.unfiled ?? []),
  ];

  return (
    <aside className="sidebar">
      <div className="sidebar-header">
        <div
          className="brand-clickable"
          onClick={onHome}
          title="Home"
        >
          <div className="brand-mark">
            <BookOpen size={20} strokeWidth={2.2} />
          </div>
          <div className="brand-text">
            <h1>Study Studio</h1>
            <p>AI-powered notes, quizzes & flashcards</p>
          </div>
        </div>
        <button
          className="ghost icon-btn"
          title="New module"
          onClick={() => setShowNewModule((v) => !v)}
        >
          <Plus size={18} />
        </button>
      </div>

      <div className="doc-list">
        {tree.isLoading && (
          <div className="loading">
            <Loader2 size={16} className="spinner" />
            Loading…
          </div>
        )}

        {/* New module input */}
        {showNewModule && (
          <div className="tree-create-input">
            <input
              autoFocus
              placeholder="Module name…"
              value={newModuleName}
              onChange={(e) => setNewModuleName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && newModuleName.trim()) {
                  createModule.mutate(newModuleName.trim());
                }
              }}
            />
          </div>
        )}

        {/* Module → Lesson → Document tree */}
        {tree.data?.modules.map((mod) => {
          const isExpanded = expanded.has(mod.id);
          return (
            <div key={mod.id} className="tree-module">
              <div className="tree-node tree-module-head" onClick={() => toggle(mod.id)}>
                <ChevronRight
                  size={14}
                  className={`tree-chevron ${isExpanded ? "expanded" : ""}`}
                />
                {isExpanded ? <FolderOpen size={15} /> : <Folder size={15} />}
                <span className="tree-label">{mod.title}</span>
                <div className="tree-actions">
                  <button
                    className="ghost icon-btn tree-action-btn"
                    title="Add lesson"
                    onClick={(e) => {
                      e.stopPropagation();
                      setAddingToModule(
                        addingToModule === mod.id ? null : mod.id,
                      );
                      if (!isExpanded) toggle(mod.id);
                    }}
                  >
                    <Plus size={14} />
                  </button>
                  <button
                    className="ghost icon-btn tree-action-btn"
                    title="Delete module"
                    onClick={(e) => {
                      e.stopPropagation();
                      if (confirm(`Delete "${mod.title}"? Lessons will be removed; documents become unfiled.`)) {
                        deleteModule.mutate(mod.id);
                      }
                    }}
                  >
                    <X size={13} />
                  </button>
                </div>
              </div>

              {isExpanded && (
                <div className="tree-children">
                  {/* New lesson input */}
                  {addingToModule === mod.id && (
                    <div className="tree-create-input tree-lesson-input">
                      <input
                        autoFocus
                        placeholder="Lesson name…"
                        onKeyDown={(e) => {
                          const val = (e.target as HTMLInputElement).value.trim();
                          if (e.key === "Enter" && val) {
                            createLesson.mutate({ moduleId: mod.id, title: val });
                          }
                        }}
                      />
                    </div>
                  )}

                  {mod.lessons.map((les) => {
                    const lesExpanded = expanded.has(les.id);
                    return (
                      <div key={les.id} className="tree-lesson">
                        <div
                          className="tree-node tree-lesson-head"
                          onClick={() => toggle(les.id)}
                        >
                          <ChevronRight
                            size={14}
                            className={`tree-chevron ${lesExpanded ? "expanded" : ""}`}
                          />
                          <span className="tree-label">{les.title}</span>
                          <span className="tree-count">{les.documents.length}</span>
                          <div className="tree-actions">
                            <button
                              className="ghost icon-btn tree-action-btn"
                              title="Delete lesson"
                              onClick={(e) => {
                                e.stopPropagation();
                                if (confirm(`Delete "${les.title}"? Documents become unfiled.`)) {
                                  deleteLesson.mutate(les.id);
                                }
                              }}
                            >
                              <X size={13} />
                            </button>
                          </div>
                        </div>

                        {lesExpanded && (
                          <div className="tree-docs">
                            {les.documents.map((doc) => (
                              <DocItem
                                key={doc.id}
                                doc={doc}
                                active={doc.id === selectedId}
                                onClick={() => onNavigate(doc.id)}
                                onDelete={() => remove.mutate(doc.id)}
                                tree={tree.data}
                                onMove={(lessonId) =>
                                  api.moveDocument(doc.id, lessonId).then(() =>
                                    queryClient.invalidateQueries({
                                      queryKey: ["module-tree"],
                                    }),
                                  )
                                }
                              />
                            ))}
                            {les.documents.length === 0 && (
                              <div className="tree-empty">No documents</div>
                            )}
                          </div>
                        )}
                      </div>
                    );
                  })}

                  {mod.lessons.length === 0 && !addingToModule && (
                    <div className="tree-empty">No lessons yet</div>
                  )}
                </div>
              )}
            </div>
          );
        })}

        {/* Unfiled documents */}
        {(tree.data?.unfiled?.length ?? 0) > 0 && (
          <div className="tree-unfiled">
            <div className="tree-unfiled-head">Unfiled</div>
            {tree.data?.unfiled.map((doc) => (
              <DocItem
                key={doc.id}
                doc={doc}
                active={doc.id === selectedId}
                onClick={() => onNavigate(doc.id)}
                onDelete={() => remove.mutate(doc.id)}
                tree={tree.data}
                onMove={(lessonId) =>
                  api.moveDocument(doc.id, lessonId).then(() =>
                    queryClient.invalidateQueries({ queryKey: ["module-tree"] }),
                  )
                }
              />
            ))}
          </div>
        )}

        {allDocs.length === 0 && !tree.isLoading && !showNewModule && (
          <div className="empty">No documents yet</div>
        )}
      </div>

      <ProfileCard />

      <div className="upload">
        <input
          ref={fileInput}
          type="file"
          accept=".pdf,.txt,.md,.webm,.mp3,.m4a,.wav,.ogg"
          style={{ display: "none" }}
          onChange={(e) => handleFiles(e.target.files)}
        />
        <div
          className={`dropzone ${dragging ? "dragging" : ""}`}
          onClick={() => !upload.isPending && fileInput.current?.click()}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            handleFiles(e.dataTransfer.files);
          }}
        >
          {upload.isPending ? (
            <>
              <Loader2 size={22} className="spinner dz-icon" />
              {uploadProgress !== null
                ? `Uploading… ${uploadProgress}%`
                : "Uploading…"}
            </>
          ) : isRecording ? (
            <>
              <div className="recording-indicator" />
              <span style={{ color: "var(--danger)", fontWeight: 600 }}>
                Recording… {formatTime(recordTime)}
              </span>
            </>
          ) : (
            <>
              <UploadCloud size={24} className="dz-icon" />
              <span>
                Drop a file or <strong>click to upload</strong>
              </span>
              <span style={{ fontSize: "0.72rem", color: "var(--text-faint)" }}>
                PDF, TXT, MD, or audio
              </span>
            </>
          )}
        </div>

        {/* Record button */}
        <button
          className={`record-btn ${isRecording ? "recording" : ""}`}
          onClick={toggleRecording}
          disabled={upload.isPending}
        >
          {isRecording ? (
            <>
              <Square size={14} />
              Stop & upload
            </>
          ) : (
            <>
              <Mic size={16} />
              Record lecture
            </>
          )}
        </button>

        {upload.isError && (
          <div className="error">
            Upload failed: {(upload.error as Error).message}
          </div>
        )}
      </div>
    </aside>
  );
}

// --- Document item with move-to-lesson menu ---

function DocItem({
  doc,
  active,
  onClick,
  onDelete,
  tree,
  onMove,
}: {
  doc: { id: string; filename: string; page_count: number; char_count: number };
  active: boolean;
  onClick: () => void;
  onDelete: () => void;
  tree: ModuleTree | undefined;
  onMove: (lessonId: string | null) => void;
}) {
  const [showMenu, setShowMenu] = useState(false);
  const allLessons =
    tree?.modules.flatMap((m) =>
      m.lessons.map((l) => ({ ...l, moduleTitle: m.title })),
    ) ?? [];

  return (
    <div className={`doc-item ${active ? "active" : ""}`} onClick={onClick}>
      <span className="doc-icon">
        <FileText size={15} />
      </span>
      <div className="meta">
        <span className="filename">{doc.filename}</span>
        <span className="sub">
          {doc.page_count > 1
            ? `${doc.page_count} pages`
            : `${(doc.char_count / 1000).toFixed(1)}k chars`}
        </span>
      </div>
      <div className="doc-item-actions">
        <button
          className="ghost icon-btn delete-btn"
          title="File into lesson"
          onClick={(e) => {
            e.stopPropagation();
            setShowMenu((v) => !v);
          }}
        >
          <FolderInput size={13} />
        </button>
        <button
          className="ghost icon-btn delete-btn"
          title="Delete document"
          onClick={(e) => {
            e.stopPropagation();
            onDelete();
          }}
        >
          <X size={14} />
        </button>
      </div>
      {showMenu && (
        <div className="move-menu" onClick={(e) => e.stopPropagation()}>
          <div className="move-menu-title">Move to…</div>
          <button
            className="move-menu-item"
            onClick={() => {
              onMove(null);
              setShowMenu(false);
            }}
          >
            Unfiled
          </button>
          {allLessons.map((l) => (
            <button
              key={l.id}
              className="move-menu-item"
              onClick={() => {
                onMove(l.id);
                setShowMenu(false);
              }}
            >
              {l.moduleTitle} → {l.title}
            </button>
          ))}
          {allLessons.length === 0 && (
            <div className="move-menu-empty">No lessons available</div>
          )}
        </div>
      )}
    </div>
  );
}
