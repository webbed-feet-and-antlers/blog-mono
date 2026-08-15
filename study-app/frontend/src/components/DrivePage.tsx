import { useRef, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useRouterState } from "@tanstack/react-router";
import {
  FolderOpen,
  Folder,
  FolderPlus,
  FileText,
  Mic,
  UploadCloud,
  Plus,
  ChevronRight,
  MoreVertical,
  Pencil,
  Trash2,
  FolderInput,
  Loader2,
  Search,
} from "lucide-react";
import * as api from "../api/client";
import { track } from "../api/track";
import { ModulePlanPanel } from "./ModulePlanPanel";
import type { Document, Lesson, Module, ModuleTree } from "../types";

/**
 * Google Drive–inspired content browser. Shows Modules and Lessons as folder
 * cards, documents as file cards, with breadcrumb navigation, drag-and-drop
 * filing, inline rename, and upload-into-folder. Reads/writes the same
 * ["module-tree"] query as the sidebar, so both stay in sync.
 *
 * Navigation depth via search params:
 *   /drive                  → root: all modules + unfiled docs
 *   /drive?module=<id>      → module: its lessons + docs across those lessons
 *   /drive?module=<id>&lesson=<id> → lesson: its documents
 */
export function DrivePage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  // Track which doc is being dragged (ref, not state — no re-render needed).
  const draggedDocId = useRef<string | null>(null);

  // Read navigation depth from the URL search params.
  const search = useRouterState({
    select: (s) => s.location.search as { module?: string; lesson?: string },
  });
  const moduleId = search.module;
  const lessonId = search.lesson;

  const [searchQuery, setSearchQuery] = useState("");
  // Debounce search telemetry (1s after the last keystroke).
  const searchDebounce = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Folder being dragged over (for visual highlight + drop logic).
  const [dragOverFolder, setDragOverFolder] = useState<string | null>(null);
  // Context menu open state: { type, id } | null
  const [menuOpen, setMenuOpen] = useState<{ type: "folder" | "doc"; id: string } | null>(null);
  // Inline rename state: { id, kind, value }
  const [renaming, setRenaming] = useState<{
    id: string;
    kind: "module" | "lesson";
    value: string;
  } | null>(null);
  // Move-to modal for a document.
  const [movingDoc, setMovingDoc] = useState<Document | null>(null);
  // New-folder modal: null = closed, true = open.
  const [showNewFolder, setShowNewFolder] = useState(false);

  const tree = useQuery({
    queryKey: ["module-tree"],
    queryFn: api.listModuleTree,
  });

  // --- Mutations (all invalidate ["module-tree"] so sidebar syncs too) ---
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["module-tree"] });

  const createModuleMut = useMutation({
    mutationFn: (title: string) => api.createModule(title),
    onSuccess: invalidate,
  });
  const createLessonMut = useMutation({
    mutationFn: ({ moduleId, title }: { moduleId: string; title: string }) =>
      api.createLesson(moduleId, title),
    onSuccess: invalidate,
  });
  const renameModuleMut = useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) =>
      api.renameModule(id, title),
    onSuccess: invalidate,
  });
  const renameLessonMut = useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) =>
      api.renameLesson(id, title),
    onSuccess: invalidate,
  });
  const deleteModuleMut = useMutation({
    mutationFn: (id: string) => api.deleteModule(id),
    onSuccess: invalidate,
  });
  const deleteLessonMut = useMutation({
    mutationFn: (id: string) => api.deleteLesson(id),
    onSuccess: invalidate,
  });
  const deleteDocMut = useMutation({
    mutationFn: (id: string) => api.deleteDocument(id),
    onSuccess: invalidate,
  });
  const moveDocMut = useMutation({
    mutationFn: ({
      docId,
      lessonId,
      moduleId,
    }: {
      docId: string;
      lessonId?: string | null;
      moduleId?: string | null;
    }) => api.moveDocument(docId, { lessonId, moduleId }),
    onSuccess: invalidate,
  });
  const uploadMut = useMutation({
    mutationFn: ({
      file,
      lessonId,
      moduleId,
    }: {
      file: File;
      lessonId?: string;
      moduleId?: string;
    }) => api.uploadDocument(file, lessonId, undefined, moduleId),
    onSuccess: invalidate,
  });

  if (tree.isLoading) {
    return (
      <div className="loading drive-loading">
        <Loader2 size={18} className="spinner" />
        Loading your drive…
      </div>
    );
  }

  const data: ModuleTree | undefined = tree.data;

  // --- Resolve current location context ---
  const currentModule = data?.modules.find((m) => m.id === moduleId);
  const currentLesson = currentModule?.lessons.find((l) => l.id === lessonId);

  // --- Build the list of folders + docs to show at this depth ---
  let folders: { id: string; title: string; count: number; kind: "module" | "lesson" }[] = [];
  let docs: Document[] = [];

  if (data) {
    if (lessonId && currentLesson) {
      // Lesson view: just documents (leaf level).
      folders = [];
      docs = currentLesson.documents;
    } else if (moduleId && currentModule) {
      // Module view: lessons as sub-folders + standalone module docs +
      // docs across lessons.
      folders = currentModule.lessons.map((l) => ({
        id: l.id,
        title: l.title,
        count: l.documents.length,
        kind: "lesson" as const,
      }));
      docs = [
        ...currentModule.lessons.flatMap((l) => l.documents),
        ...currentModule.documents,
      ];
    } else {
      // Root view: modules as folders + unfiled docs.
      folders = data.modules.map((m) => ({
        id: m.id,
        title: m.title,
        count:
          m.lessons.reduce((sum, l) => sum + l.documents.length, 0) +
          m.documents.length,
        kind: "module" as const,
      }));
      docs = data.unfiled;
    }
  }

  // --- Search filtering (when query is non-empty, search across EVERYTHING) ---
  const isSearching = searchQuery.trim().length > 0;
  if (isSearching && data) {
    const q = searchQuery.toLowerCase();
    const allModules = data.modules.map((m) => ({
      id: m.id,
      title: m.title,
      count:
        m.lessons.reduce((sum, l) => sum + l.documents.length, 0) +
        m.documents.length,
      kind: "module" as const,
    }));
    folders = allModules.filter((f) => f.title.toLowerCase().includes(q));
    docs = [
      ...data.unfiled,
      ...data.modules.flatMap((m) => [
        ...m.lessons.flatMap((l) => l.documents),
        ...m.documents,
      ]),
    ].filter((d) => d.filename.toLowerCase().includes(q));
  }

  // --- Navigation helpers ---
  function goToRoot() {
    track("navigation.moved", { to: "drive" });
    navigate({ to: "/drive" });
  }
  function goToModule(id: string) {
    track("navigation.moved", { to: "drive-module" });
    navigate({ to: "/drive", search: { module: id } });
  }
  function goToLesson(modId: string, lesId: string) {
    track("navigation.moved", { to: "drive-lesson" });
    navigate({ to: "/drive", search: { module: modId, lesson: lesId } });
  }

  // --- New folder (opens a modal) ---
  function handleNewFolder() {
    setShowNewFolder(true);
  }
  function commitNewFolder(title: string) {
    const trimmed = title.trim();
    if (!trimmed) return;
    if (moduleId) {
      createLessonMut.mutate({ moduleId, title: trimmed });
    } else {
      createModuleMut.mutate(trimmed);
    }
    setShowNewFolder(false);
  }

  // --- Upload ---
  function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    // File into the current context: lesson if inside a lesson, module if
    // inside a module, otherwise unfiled.
    uploadMut.mutate({
      file,
      lessonId: lessonId ?? undefined,
      moduleId: !lessonId ? moduleId ?? undefined : undefined,
    });
    e.target.value = ""; // reset for re-upload
  }

  // --- Drag & drop ---

  function onDocDragStart(e: React.DragEvent, docId: string) {
    draggedDocId.current = docId;
    e.dataTransfer.effectAllowed = "move";
  }
  function onFolderDragOver(e: React.DragEvent, folderId: string) {
    if (!draggedDocId.current) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    setDragOverFolder(folderId);
  }
  function onFolderDragLeave(folderId: string) {
    setDragOverFolder((prev) => (prev === folderId ? null : prev));
  }
  function onFolderDrop(e: React.DragEvent, folder: { id: string; kind: "module" | "lesson" }) {
    e.preventDefault();
    setDragOverFolder(null);
    const docId = draggedDocId.current;
    draggedDocId.current = null;
    if (!docId) return;
    if (folder.kind === "lesson") {
      moveDocMut.mutate({ docId, lessonId: folder.id });
    } else {
      // Dropped on a module card — file the doc directly under the module.
      moveDocMut.mutate({ docId, moduleId: folder.id });
    }
  }
  function onUnfiledDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragOverFolder(null);
    const docId = draggedDocId.current;
    draggedDocId.current = null;
    if (docId) moveDocMut.mutate({ docId, lessonId: null });
  }

  // --- Rename save ---
  function commitRename() {
    if (!renaming) return;
    const { id, kind, value } = renaming;
    const title = value.trim();
    if (title) {
      if (kind === "module") renameModuleMut.mutate({ id, title });
      else renameLessonMut.mutate({ id, title });
    }
    setRenaming(null);
  }

  // --- Delete ---
  function handleDeleteFolder(folder: { id: string; kind: "module" | "lesson"; title: string }) {
    if (window.confirm(`Delete "${folder.title}"? Documents will be moved to Unfiled.`)) {
      if (folder.kind === "module") deleteModuleMut.mutate(folder.id);
      else deleteLessonMut.mutate(folder.id);
    }
  }
  function handleDeleteDoc(doc: Document) {
    if (window.confirm(`Delete "${doc.filename}"?`)) {
      deleteDocMut.mutate(doc.id);
    }
  }

  return (
    <div className="drive-page">
      {/* Toolbar */}
      <div className="drive-toolbar">
        <div className="drive-breadcrumb">
          <button type="button" className="crumb" onClick={goToRoot}>
            My Drive
          </button>
          {currentModule && (
            <>
              <ChevronRight size={14} className="crumb-sep" />
              {lessonId && currentLesson ? (
                <button
                  type="button"
                  className="crumb"
                  onClick={() => goToModule(currentModule.id)}
                >
                  {currentModule.title}
                </button>
              ) : (
                <span className="crumb current">{currentModule.title}</span>
              )}
            </>
          )}
          {currentLesson && (
            <>
              <ChevronRight size={14} className="crumb-sep" />
              <span className="crumb current">{currentLesson.title}</span>
            </>
          )}
        </div>

        <div className="drive-actions">
          <div className="drive-search-wrapper">
            <Search size={14} className="drive-search-icon" />
            <input
              type="text"
              className="drive-search"
              placeholder="Search drive…"
              value={searchQuery}
              onChange={(e) => {
                setSearchQuery(e.target.value);
                const q = e.target.value.trim();
                if (q) {
                  if (searchDebounce.current) clearTimeout(searchDebounce.current);
                  searchDebounce.current = setTimeout(
                    () => track("drive.searched", { query: q.slice(0, 100) }),
                    1000,
                  );
                }
              }}
            />
          </div>
          <button
            type="button"
            className="ghost icon-btn"
            title="Upload to this folder"
            onClick={() => fileInput.current?.click()}
            disabled={uploadMut.isPending}
          >
            {uploadMut.isPending ? (
              <Loader2 size={16} className="spinner" />
            ) : (
              <UploadCloud size={16} />
            )}
          </button>
          {!lessonId && (
            <button
              type="button"
              className="primary drive-new-folder-btn"
              onClick={handleNewFolder}
            >
              <Plus size={16} />
              New folder
            </button>
          )}
          <input
            ref={fileInput}
            type="file"
            accept=".pdf,.txt,.md,.pptx,.docx,.xlsx,.doc,.ppt,.xls,.webm,.mp3,.m4a,.wav,.ogg"
            style={{ display: "none" }}
            onChange={handleUpload}
          />
        </div>
      </div>

      {/* Empty states */}
      {folders.length === 0 && docs.length === 0 && !isSearching && (
        <div className="empty drive-empty">
          {moduleId
            ? "This folder is empty. Upload a document or create a sub-folder."
            : "No folders yet. Create one with 'New folder', or upload a document."}
        </div>
      )}
      {isSearching && folders.length === 0 && docs.length === 0 && (
        <div className="empty drive-empty">
          No documents match "{searchQuery}".
        </div>
      )}

      {/* Module study plan — the agent's adaptive plan for this module */}
      {moduleId && !lessonId && currentModule && !isSearching && (
        <ModulePlanPanel module={currentModule} />
      )}

      {/* Folder grid */}
      {folders.length > 0 && (
        <>
          <h2 className="drive-section-title">
            {moduleId ? "Lessons" : "Modules"}
          </h2>
          <div className="drive-grid">
            {folders.map((f) => {
              const isRenaming = renaming?.id === f.id;
              const menuForThis = menuOpen?.type === "folder" && menuOpen.id === f.id;
              return (
                <div
                  key={f.id}
                  className={`drive-card folder ${dragOverFolder === f.id ? "drag-over" : ""}`}
                  onClick={() => {
                    if (isRenaming) return;
                    if (f.kind === "module") goToModule(f.id);
                    else if (moduleId) goToLesson(moduleId, f.id);
                  }}
                  onDragOver={(e) => onFolderDragOver(e, f.id)}
                  onDragLeave={() => onFolderDragLeave(f.id)}
                  onDrop={(e) => onFolderDrop(e, f)}
                >
                  <button
                    type="button"
                    className="drive-card-menu"
                    onClick={(e) => {
                      e.stopPropagation();
                      setMenuOpen(menuForThis ? null : { type: "folder", id: f.id });
                    }}
                  >
                    <MoreVertical size={14} />
                  </button>
                  {menuForThis && (
                    <div className="drive-card-menu-dropdown" onClick={(e) => e.stopPropagation()}>
                      <button
                        type="button"
                        onClick={() => {
                          setMenuOpen(null);
                          setRenaming({
                            id: f.id,
                            kind: f.kind,
                            value: f.title,
                          });
                        }}
                      >
                        <Pencil size={13} />
                        Rename
                      </button>
                      <button
                        type="button"
                        className="danger"
                        onClick={() => {
                          setMenuOpen(null);
                          handleDeleteFolder(f);
                        }}
                      >
                        <Trash2 size={13} />
                        Delete
                      </button>
                    </div>
                  )}
                  <div className="drive-card-icon folder-icon">
                    <FolderOpen size={28} />
                  </div>
                  {isRenaming ? (
                    <input
                      autoFocus
                      className="drive-rename-input"
                      value={renaming.value}
                      onChange={(e) =>
                        setRenaming((r) => (r ? { ...r, value: e.target.value } : r))
                      }
                      onClick={(e) => e.stopPropagation()}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") commitRename();
                        if (e.key === "Escape") setRenaming(null);
                      }}
                      onBlur={commitRename}
                    />
                  ) : (
                    <div className="drive-card-title">{f.title}</div>
                  )}
                  <div className="drive-card-subtitle">
                    {f.count} {f.count === 1 ? "item" : "items"}
                  </div>
                </div>
              );
            })}
          </div>
        </>
      )}

      {/* Documents grid */}
      {docs.length > 0 && (
        <>
          <h2 className="drive-section-title">
            {isSearching ? "Results" : "Documents"}
          </h2>
          <div
            className={`drive-grid ${moduleId && !lessonId && !isSearching ? "unfiled-drop-zone" : ""}`}
            onDragOver={(e) => {
              if (draggedDocId.current) e.preventDefault();
            }}
            onDrop={onUnfiledDrop}
          >
            {docs.map((d) => {
              const isAudio = d.kind === "audio";
              const menuForThis = menuOpen?.type === "doc" && menuOpen.id === d.id;
              return (
                <div
                  key={d.id}
                  className="drive-card doc"
                  draggable
                  onDragStart={(e) => onDocDragStart(e, d.id)}
                  onClick={() => navigate({ to: "/documents/$docId", params: { docId: d.id } })}
                >
                  <button
                    type="button"
                    className="drive-card-menu"
                    onClick={(e) => {
                      e.stopPropagation();
                      setMenuOpen(menuForThis ? null : { type: "doc", id: d.id });
                    }}
                  >
                    <MoreVertical size={14} />
                  </button>
                  {menuForThis && (
                    <div className="drive-card-menu-dropdown" onClick={(e) => e.stopPropagation()}>
                      <button
                        type="button"
                        onClick={() => {
                          setMenuOpen(null);
                          setMovingDoc(d);
                        }}
                      >
                        <FolderInput size={13} />
                        Move to…
                      </button>
                      <button
                        type="button"
                        className="danger"
                        onClick={() => {
                          setMenuOpen(null);
                          handleDeleteDoc(d);
                        }}
                      >
                        <Trash2 size={13} />
                        Delete
                      </button>
                    </div>
                  )}
                  <div className={`drive-card-icon ${isAudio ? "audio-icon" : "doc-icon"}`}>
                    {isAudio ? <Mic size={26} /> : <FileText size={26} />}
                  </div>
                  <div className="drive-card-title">{d.filename}</div>
                  <div className="drive-card-subtitle">
                    {d.topic
                      ? d.topic
                      : isAudio
                        ? d.duration_seconds
                          ? `${Math.round(d.duration_seconds / 60)} min`
                          : "Audio"
                        : d.page_count > 1
                          ? `${d.page_count} pages`
                          : "1 page"}
                  </div>
                </div>
              );
            })}
          </div>
        </>
      )}

      {/* Move-to modal */}
      {movingDoc && data && (
        <MoveToModal
          doc={movingDoc}
          tree={data}
          onClose={() => setMovingDoc(null)}
          onMove={(target) => {
            moveDocMut.mutate({ docId: movingDoc.id, ...target });
            setMovingDoc(null);
          }}
        />
      )}

      {/* New folder modal */}
      {showNewFolder && (
        <NewFolderModal
          isLesson={!!moduleId}
          parentTitle={currentModule?.title}
          isPending={
            createModuleMut.isPending || createLessonMut.isPending
          }
          onClose={() => setShowNewFolder(false)}
          onCreate={commitNewFolder}
        />
      )}
    </div>
  );
}

// --- New folder modal -----------------------------------------------------

function NewFolderModal({
  isLesson,
  parentTitle,
  isPending,
  onClose,
  onCreate,
}: {
  isLesson: boolean;
  parentTitle?: string;
  isPending: boolean;
  onClose: () => void;
  onCreate: (title: string) => void;
}) {
  const [title, setTitle] = useState("");

  const label = isLesson ? "New lesson" : "New module";
  const placeholder = isLesson ? "Lesson name…" : "Module name…";

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal-content new-folder-modal"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-header">
          <FolderPlus size={20} />
          <h3>{label}</h3>
        </div>
        {isLesson && parentTitle && (
          <p className="new-folder-context">in {parentTitle}</p>
        )}
        <input
          autoFocus
          type="text"
          className="new-folder-input"
          placeholder={placeholder}
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") onCreate(title);
            if (e.key === "Escape") onClose();
          }}
        />
        <div className="modal-actions">
          <button type="button" className="ghost" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="primary"
            onClick={() => onCreate(title)}
            disabled={!title.trim() || isPending}
          >
            {isPending ? (
              <>
                <Loader2 size={14} className="spinner" />
                Creating…
              </>
            ) : (
              "Create"
            )}
          </button>
        </div>
      </div>
    </div>
  );
}

// --- Move-to modal: lists modules (as targets) + their lessons + Unfiled ---

function MoveToModal({
  doc,
  tree,
  onClose,
  onMove,
}: {
  doc: Document;
  tree: ModuleTree;
  onClose: () => void;
  onMove: (target: { lessonId?: string | null; moduleId?: string | null }) => void;
}) {
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-content move-to-modal" onClick={(e) => e.stopPropagation()}>
        <h3>Move "{doc.filename}"</h3>
        <div className="move-to-list">
          <button
            type="button"
            className="move-to-option"
            onClick={() => onMove({ lessonId: null, moduleId: null })}
          >
            <FolderInput size={15} />
            Unfiled
          </button>
          {tree.modules.map((m: Module) => (
            <div key={m.id} className="move-to-group">
              <button
                type="button"
                className="move-to-option module-root"
                onClick={() => onMove({ moduleId: m.id })}
              >
                <Folder size={15} />
                {m.title}
                <span className="move-to-module-label">module root</span>
              </button>
              {m.lessons.map((l: Lesson) => (
                <button
                  key={l.id}
                  type="button"
                  className="move-to-option sub"
                  onClick={() => onMove({ lessonId: l.id })}
                >
                  <FolderOpen size={14} />
                  {l.title}
                </button>
              ))}
              {m.lessons.length === 0 && (
                <div className="move-to-empty">No lessons</div>
              )}
            </div>
          ))}
        </div>
        <button type="button" className="ghost" onClick={onClose}>
          Cancel
        </button>
      </div>
    </div>
  );
}
