import { type ReactNode, useRef, useState } from "react";
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
  ChevronDown,
  CalendarClock,
  MoreVertical,
  Pencil,
  Trash2,
  FolderInput,
  Loader2,
  Search,
} from "lucide-react";
import * as api from "../api/client";
import { track } from "../api/track";
import { FileToModuleModal } from "./FileToModuleModal";
import { ModulePlanPanel } from "./ModulePlanPanel";
import type { Document, Lesson, Module, ModuleTree } from "../types";

/**
 * Module browser organized by semester. Shows Modules and Lessons as folder
 * cards, documents as file cards, with breadcrumb navigation, drag-and-drop
 * filing, inline rename, and upload-into-folder. Reads/writes the same
 * ["module-tree"] query as the sidebar, so both stay in sync.
 *
 * Navigation depth via search params:
 *   /modules                → root: all modules + unfiled docs
 *   /modules?module=<id>      → module: its lessons + docs across those lessons
 *   /modules?module=<id>&lesson=<id> → lesson: its documents
 */
export function ModulesPage() {
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
  // Edit-details modal for a module (semester / exam date / title).
  const [editingModule, setEditingModule] = useState<Module | null>(null);
  // A picked file awaiting a module choice (root view only — inside a
  // module/lesson the context is already known).
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  // Semester group expansion overrides: current group is expanded by default,
  // older groups collapsed — toggling writes here.
  const [groupExpanded, setGroupExpanded] = useState<Record<string, boolean>>({});

  const tree = useQuery({
    queryKey: ["module-tree"],
    queryFn: api.listModuleTree,
  });

  // --- Mutations (all invalidate ["module-tree"] so sidebar syncs too) ---
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["module-tree"] });

  const createModuleMut = useMutation({
    mutationFn: ({ title, meta }: { title: string; meta?: api.ModuleMeta }) =>
      api.createModule(title, meta),
    onSuccess: invalidate,
  });
  const updateModuleMut = useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: api.ModuleMeta & { title?: string } }) =>
      api.updateModule(id, patch),
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
        Loading your modules…
      </div>
    );
  }

  const data: ModuleTree | undefined = tree.data;

  // --- Resolve current location context ---
  const currentModule = data?.modules.find((m) => m.id === moduleId);
  const currentLesson = currentModule?.lessons.find((l) => l.id === lessonId);

  // --- Build the list of folders + docs to show at this depth ---
  let folders: {
    id: string;
    title: string;
    count: number;
    kind: "module" | "lesson";
    academic_year?: string | null;
    term?: string | null;
  }[] = [];
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
        academic_year: m.academic_year ?? null,
        term: m.term ?? null,
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

  // --- Semester organization (root view) ---
  const yearOptions = buildYearOptions(data?.modules ?? []);
  const defaultNewModuleMeta = defaultSemesterFor(data?.modules ?? []);

  // --- Navigation helpers ---
  function goToRoot() {
    track("navigation.moved", { to: "modules" });
    navigate({ to: "/modules" });
  }
  function goToModule(id: string) {
    track("navigation.moved", { to: "modules-module" });
    navigate({ to: "/modules", search: { module: id } });
  }
  function goToLesson(modId: string, lesId: string) {
    track("navigation.moved", { to: "modules-lesson" });
    navigate({ to: "/modules", search: { module: modId, lesson: lesId } });
  }

  // --- New module / lesson modal ---
  function handleNewFolder() {
    setShowNewFolder(true);
  }
  function commitNewFolder(title: string, meta?: api.ModuleMeta) {
    const trimmed = title.trim();
    if (!trimmed) return;
    if (moduleId) {
      createLessonMut.mutate({ moduleId, title: trimmed });
    } else {
      createModuleMut.mutate({ title: trimmed, meta });
    }
    setShowNewFolder(false);
  }

  // --- Upload ---
  function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    if (!moduleId) {
      // Root view: prompt for a module instead of filing to Unfiled.
      setPendingFile(file);
    } else {
      // File into the current context: lesson if inside a lesson, module
      // if inside a module.
      uploadMut.mutate({
        file,
        lessonId: lessonId ?? undefined,
        moduleId: !lessonId ? moduleId : undefined,
      });
    }
    e.target.value = ""; // reset for re-upload
  }

  function uploadWith(target: { moduleId?: string; lessonId?: string } | null) {
    const file = pendingFile;
    setPendingFile(null);
    if (!file) return;
    uploadMut.mutate({
      file,
      lessonId: target?.lessonId,
      moduleId: target && !target.lessonId ? target.moduleId : undefined,
    });
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

  // One folder card (module or lesson) — shared by the grouped root view,
  // the lesson view, and search results.
  function renderFolderCard(f: (typeof folders)[number]) {
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
            {f.kind === "module" && (
              <button
                type="button"
                onClick={() => {
                  setMenuOpen(null);
                  const moduleObj = data?.modules.find((m) => m.id === f.id);
                  if (moduleObj) setEditingModule(moduleObj);
                }}
              >
                <CalendarClock size={13} />
                Edit details
              </button>
            )}
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
  }

  return (
    <div className="drive-page">
      {/* Toolbar */}
      <div className="drive-toolbar">
        <div className="drive-breadcrumb">
          <button type="button" className="crumb" onClick={goToRoot}>
            Modules
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
              placeholder="Search modules…"
              value={searchQuery}
              onChange={(e) => {
                setSearchQuery(e.target.value);
                const q = e.target.value.trim();
                if (q) {
                  if (searchDebounce.current) clearTimeout(searchDebounce.current);
                  searchDebounce.current = setTimeout(
                    () => track("modules.searched", { query: q.slice(0, 100) }),
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
              {lessonId ? "New lesson" : "New module"}
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
            : "No modules yet. Create one with 'New module', or upload a document."}
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

      {/* Folder cards: grouped by semester at root, flat inside modules/search */}
      {folders.length > 0 && !moduleId && !isSearching && (
        <SemesterGroups
          folders={folders}
          groupExpanded={groupExpanded}
          onToggle={(key, next) =>
            setGroupExpanded((prev) => ({ ...prev, [key]: next }))
          }
          renderCard={renderFolderCard}
        />
      )}
      {folders.length > 0 && (moduleId || isSearching) && (
        <>
          <h2 className="drive-section-title">
            {moduleId ? "Lessons" : "Modules"}
          </h2>
          <div className="drive-grid">{folders.map(renderFolderCard)}</div>
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

      {/* New module / lesson modal */}
      {showNewFolder && (
        <NewFolderModal
          isLesson={!!moduleId}
          parentTitle={currentModule?.title}
          isPending={
            createModuleMut.isPending || createLessonMut.isPending
          }
          yearOptions={yearOptions}
          defaultMeta={defaultNewModuleMeta}
          onClose={() => setShowNewFolder(false)}
          onCreate={commitNewFolder}
        />
      )}
      {pendingFile && (
        <FileToModuleModal noun="document" onSelect={uploadWith} />
      )}
      {editingModule && (
        <EditModuleModal
          module={editingModule}
          isPending={updateModuleMut.isPending}
          yearOptions={yearOptions}
          onClose={() => setEditingModule(null)}
          onSave={(patch) => {
            updateModuleMut.mutate(
              { id: editingModule.id, patch },
              { onSuccess: () => setEditingModule(null) },
            );
          }}
        />
      )}
    </div>
  );
}

// --- {lessonId ? "New lesson" : "New module"} modal -----------------------------------------------------

function NewFolderModal({
  isLesson,
  parentTitle,
  isPending,
  yearOptions,
  defaultMeta,
  onClose,
  onCreate,
}: {
  isLesson: boolean;
  parentTitle?: string;
  isPending: boolean;
  yearOptions: string[];
  defaultMeta: { academic_year: string | null; term: string | null };
  onClose: () => void;
  onCreate: (title: string, meta?: api.ModuleMeta) => void;
}) {
  const [title, setTitle] = useState("");
  // Semester fields only apply to modules; lessons inherit their module's place.
  const [term, setTerm] = useState(isLesson ? "" : defaultMeta.term ?? "");
  const [academicYear, setAcademicYear] = useState(
    isLesson ? "" : defaultMeta.academic_year ?? "",
  );
  const [examDate, setExamDate] = useState("");

  const label = isLesson ? "New lesson" : "New module";
  const placeholder = isLesson ? "Lesson name…" : "Module name…";

  function handleCreate() {
    onCreate(
      title,
      isLesson
        ? undefined
        : {
            term: term || null,
            academic_year: academicYear || null,
            exam_date: examDate || null,
          },
    );
  }

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
            if (e.key === "Enter") handleCreate();
            if (e.key === "Escape") onClose();
          }}
        />
        {!isLesson && (
          <>
            <div className="form-row">
              <label className="form-label">
                Term
                <select value={term} onChange={(e) => setTerm(e.target.value)}>
                  <option value="">—</option>
                  <option value="Autumn">Autumn</option>
                  <option value="Spring">Spring</option>
                  <option value="Summer">Summer</option>
                </select>
              </label>
              <label className="form-label">
                Academic year
                <select
                  value={academicYear}
                  onChange={(e) => setAcademicYear(e.target.value)}
                >
                  <option value="">—</option>
                  {yearOptions.map((y) => (
                    <option key={y} value={y}>
                      {y}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <label className="form-label">
              Exam date (optional — paces the study plan)
              <input
                type="date"
                value={examDate}
                onChange={(e) => setExamDate(e.target.value)}
              />
            </label>
          </>
        )}
        <div className="modal-actions">
          <button type="button" className="ghost" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="primary"
            onClick={handleCreate}
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

// --- Semester grouping helpers -----------------------------------------------
// Canonical term order within an academic year: Autumn/Fall first, then
// Spring, then Summer. Unknown labels sort after, alphabetically.

const TERM_ORDER: Record<string, number> = {
  autumn: 0,
  fall: 0,
  spring: 1,
  summer: 2,
};

interface SemesterGroup {
  key: string;
  label: string;
  yearStart: number;
  termOrder: number;
  folders: {
    id: string;
    title: string;
    count: number;
    kind: "module" | "lesson";
    academic_year?: string | null;
    term?: string | null;
  }[];
}

function yearStartOf(academicYear: string | null | undefined): number {
  const n = parseInt((academicYear ?? "").slice(0, 4), 10);
  return Number.isNaN(n) ? 0 : n;
}

function termOrderOf(term: string | null | undefined): number {
  return TERM_ORDER[(term ?? "").toLowerCase()] ?? 3;
}

/** The academic year containing today, as "2026/27". */
function currentAcademicYear(): string {
  const now = new Date();
  // Academic years roll over around August — before then it's still the
  // previous year's cycle (e.g. July 2026 → "2025/26").
  const start = now.getMonth() >= 7 ? now.getFullYear() : now.getFullYear() - 1;
  return `${start}/${String(start + 1).slice(2)}`;
}

/** Default semester for a NEW module: the latest semester already in use,
 * else the current calendar academic year with no term chosen. */
function defaultSemesterFor(modules: Module[]): {
  academic_year: string | null;
  term: string | null;
} {
  const assigned = modules.filter((m) => m.academic_year);
  if (assigned.length === 0) {
    return { academic_year: currentAcademicYear(), term: null };
  }
  assigned.sort(
    (a, b) =>
      yearStartOf(b.academic_year) - yearStartOf(a.academic_year) ||
      termOrderOf(b.term) - termOrderOf(a.term),
  );
  return {
    academic_year: assigned[0].academic_year ?? null,
    term: assigned[0].term ?? null,
  };
}

/** Year options for pickers: years seen on modules ∪ current year ± 1. */
function buildYearOptions(modules: Module[]): string[] {
  const years = new Set<string>([currentAcademicYear()]);
  const cur = yearStartOf(currentAcademicYear());
  years.add(`${cur - 1}/${String(cur).slice(2)}`);
  years.add(`${cur + 1}/${String(cur + 2).slice(2)}`);
  for (const m of modules) {
    if (m.academic_year) years.add(m.academic_year);
  }
  return [...years].sort((a, b) => yearStartOf(b) - yearStartOf(a));
}

/** Group module folders by (academic_year, term), newest first. The first
 * group is the current semester; unassigned modules get a trailing group. */
function semesterGroups(folders: SemesterGroup["folders"]): SemesterGroup[] {
  const byKey = new Map<string, SemesterGroup>();
  for (const f of folders) {
    if (f.kind !== "module") continue;
    const hasSemester = !!f.academic_year || !!f.term;
    const key = hasSemester
      ? `${f.academic_year ?? ""}·${f.term ?? ""}`
      : "__unsorted__";
    if (!byKey.has(key)) {
      byKey.set(key, {
        key,
        label: hasSemester
          ? [f.term, f.academic_year].filter(Boolean).join(" ")
          : "No semester set",
        yearStart: hasSemester ? yearStartOf(f.academic_year) : -1,
        termOrder: hasSemester ? termOrderOf(f.term) : -1,
        folders: [],
      });
    }
    byKey.get(key)!.folders.push(f);
  }
  const groups = [...byKey.values()];
  // Newest semester first; the unsorted bucket sinks to the bottom.
  groups.sort((a, b) => {
    if (a.key === "__unsorted__") return 1;
    if (b.key === "__unsorted__") return -1;
    return b.yearStart - a.yearStart || b.termOrder - a.termOrder;
  });
  return groups;
}

/** Collapsible semester sections. The first (current) group starts expanded;
 * older groups start collapsed — overrides come from the parent's state. */
function SemesterGroups({
  folders,
  groupExpanded,
  onToggle,
  renderCard,
}: {
  folders: SemesterGroup["folders"];
  groupExpanded: Record<string, boolean>;
  onToggle: (key: string, next: boolean) => void;
  renderCard: (f: SemesterGroup["folders"][number]) => ReactNode;
}) {
  const groups = semesterGroups(folders);
  return (
    <div className="semester-groups">
      {groups.map((g, gi) => {
        const isCurrent = g.key !== "__unsorted__" && gi === 0;
        // Current semester and unsorted start expanded; older groups collapsed.
        const startsExpanded = isCurrent || g.key === "__unsorted__";
        const expanded = startsExpanded
          ? groupExpanded[g.key] !== false
          : groupExpanded[g.key] === true;
        return (
          <section key={g.key} className="semester-group">
            <button
              type="button"
              className={`semester-group-header ${isCurrent ? "current" : ""}`}
              onClick={() => onToggle(g.key, !expanded)}
            >
              <ChevronDown
                size={14}
                className="semester-group-chevron"
                style={{ transform: expanded ? "" : "rotate(-90deg)" }}
              />
              <span className="semester-group-label">{g.label}</span>
              {isCurrent && (
                <span className="semester-group-tag">This semester</span>
              )}
              <span className="semester-group-count">
                {g.folders.length} module{g.folders.length === 1 ? "" : "s"}
              </span>
            </button>
            {expanded && (
              <div className="drive-grid">{g.folders.map(renderCard)}</div>
            )}
          </section>
        );
      })}
    </div>
  );
}

// --- Edit module details (semester / exam date / title) ------------------------

function EditModuleModal({
  module,
  isPending,
  yearOptions,
  onClose,
  onSave,
}: {
  module: Module;
  isPending: boolean;
  yearOptions: string[];
  onClose: () => void;
  onSave: (patch: {
    title?: string;
    academic_year?: string | null;
    term?: string | null;
    exam_date?: string | null;
  }) => void;
}) {
  const [title, setTitle] = useState(module.title);
  const [term, setTerm] = useState<string>(module.term ?? "");
  const [academicYear, setAcademicYear] = useState<string>(
    module.academic_year ?? "",
  );
  const [examDate, setExamDate] = useState<string>(module.exam_date ?? "");

  function handleSave() {
    onSave({
      title: title.trim() || module.title,
      term: term || null,
      academic_year: academicYear || null,
      exam_date: examDate || null,
    });
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-content" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h3>Module details</h3>
        </div>
        <label className="form-label">
          Title
          <input
            autoFocus
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
        </label>
        <div className="form-row">
          <label className="form-label">
            Term
            <select value={term} onChange={(e) => setTerm(e.target.value)}>
              <option value="">—</option>
              <option value="Autumn">Autumn</option>
              <option value="Spring">Spring</option>
              <option value="Summer">Summer</option>
            </select>
          </label>
          <label className="form-label">
            Academic year
            <select
              value={academicYear}
              onChange={(e) => setAcademicYear(e.target.value)}
            >
              <option value="">—</option>
              {yearOptions.map((y) => (
                <option key={y} value={y}>
                  {y}
                </option>
              ))}
            </select>
          </label>
        </div>
        <label className="form-label">
          Exam date (paces the study plan)
          <input
            type="date"
            value={examDate}
            onChange={(e) => setExamDate(e.target.value)}
          />
        </label>
        <div className="modal-actions">
          <button className="ghost" onClick={onClose}>
            Cancel
          </button>
          <button className="primary" disabled={isPending} onClick={handleSave}>
            {isPending ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}
