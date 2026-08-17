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
  ChevronDown,
  CalendarClock,
  MoreVertical,
  Pencil,
  Trash2,
  FolderInput,
  Search,
} from "lucide-react";
import * as api from "../api/client";
import { track } from "../api/track";
import { toast } from "sonner";
import { FileToModuleModal } from "./FileToModuleModal";
import { ModulePlanPanel } from "./ModulePlanPanel";
import type { Document, Lesson, Module, ModuleTree } from "../types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

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
  // Destructive confirm target for deletes (folder or document).
  const [deleteTarget, setDeleteTarget] = useState<
    | { kind: "module" | "lesson"; id: string; title: string }
    | { kind: "doc"; id: string; title: string }
    | null
  >(null);
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
    onSuccess: (_d, { title }) => {
      invalidate();
      toast.success("Module created", { description: title });
    },
  });
  const updateModuleMut = useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: api.ModuleMeta & { title?: string } }) =>
      api.updateModule(id, patch),
    onSuccess: () => {
      invalidate();
      toast.success("Module updated");
    },
  });
  const createLessonMut = useMutation({
    mutationFn: ({ moduleId, title }: { moduleId: string; title: string }) =>
      api.createLesson(moduleId, title),
    onSuccess: (_d, { title }) => {
      invalidate();
      toast.success("Lesson created", { description: title });
    },
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
    onSuccess: () => {
      invalidate();
      toast.success("Module deleted");
    },
  });
  const deleteLessonMut = useMutation({
    mutationFn: (id: string) => api.deleteLesson(id),
    onSuccess: () => {
      invalidate();
      toast.success("Lesson deleted");
    },
  });
  const deleteDocMut = useMutation({
    mutationFn: (id: string) => api.deleteDocument(id),
    onSuccess: () => {
      invalidate();
      toast.success("Document deleted");
    },
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
    onSuccess: () => {
      invalidate();
      toast.success("Document moved");
    },
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
    onSuccess: (doc) => {
      invalidate();
      toast.success("Document uploaded", { description: doc.filename });
    },
  });

  if (tree.isLoading) {
    return (
      <div className="loading drive-loading">
        <Spinner className="size-[18px]" />
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
  function confirmDelete() {
    if (!deleteTarget) return;
    if (deleteTarget.kind === "module") deleteModuleMut.mutate(deleteTarget.id);
    else if (deleteTarget.kind === "lesson") deleteLessonMut.mutate(deleteTarget.id);
    else deleteDocMut.mutate(deleteTarget.id);
    setDeleteTarget(null);
  }

  // One folder card (module or lesson) — shared by the grouped root view,
  // the lesson view, and search results.
  function renderFolderCard(f: (typeof folders)[number]) {
    const isRenaming = renaming?.id === f.id;
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
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="outline"
              size="icon-xs"
              className="drive-card-menu"
              aria-label="Folder actions"
              onClick={(e) => e.stopPropagation()}
            >
              <MoreVertical size={14} />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" onClick={(e) => e.stopPropagation()}>
            {f.kind === "module" && (
              <DropdownMenuItem
                onClick={() => {
                  const moduleObj = data?.modules.find((m) => m.id === f.id);
                  if (moduleObj) setEditingModule(moduleObj);
                }}
              >
                <CalendarClock size={13} />
                Edit details
              </DropdownMenuItem>
            )}
            <DropdownMenuItem
              onClick={() =>
                setRenaming({
                  id: f.id,
                  kind: f.kind,
                  value: f.title,
                })
              }
            >
              <Pencil size={13} />
              Rename
            </DropdownMenuItem>
            <DropdownMenuItem
              variant="destructive"
              onClick={() =>
                setDeleteTarget({ kind: f.kind, id: f.id, title: f.title })
              }
            >
              <Trash2 size={13} />
              Delete
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
        <div className="drive-card-icon folder-icon">
          <FolderOpen size={28} />
        </div>
        {isRenaming ? (
          <Input
            autoFocus
            className="drive-rename-input h-7 w-full text-xs"
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
        <Breadcrumb>
          <BreadcrumbList className="min-w-0 gap-1">
            <BreadcrumbItem>
              <BreadcrumbLink asChild>
                <button
                  type="button"
                  className="rounded px-1 py-0.5 text-sm font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                  onClick={goToRoot}
                >
                  Modules
                </button>
              </BreadcrumbLink>
            </BreadcrumbItem>
            {currentModule && (
              <>
                <BreadcrumbSeparator className="[&>svg]:size-3.5 text-muted-foreground/50" />
                <BreadcrumbItem>
                  {lessonId && currentLesson ? (
                    <BreadcrumbLink asChild>
                      <button
                        type="button"
                        className="rounded px-1 py-0.5 text-sm font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                        onClick={() => goToModule(currentModule.id)}
                      >
                        {currentModule.title}
                      </button>
                    </BreadcrumbLink>
                  ) : (
                    <BreadcrumbPage className="px-1 text-sm font-semibold">
                      {currentModule.title}
                    </BreadcrumbPage>
                  )}
                </BreadcrumbItem>
              </>
            )}
            {currentLesson && (
              <>
                <BreadcrumbSeparator className="[&>svg]:size-3.5 text-muted-foreground/50" />
                <BreadcrumbItem>
                  <BreadcrumbPage className="px-1 text-sm font-semibold">
                    {currentLesson.title}
                  </BreadcrumbPage>
                </BreadcrumbItem>
              </>
            )}
          </BreadcrumbList>
        </Breadcrumb>

        <div className="drive-actions">
          <div className="drive-search-wrapper relative">
            <Search
              size={14}
              className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-muted-foreground"
            />
            <Input
              type="text"
              className="drive-search h-8 w-56 pl-8 text-xs"
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
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                onClick={() => fileInput.current?.click()}
                disabled={uploadMut.isPending}
                aria-label="Upload to this folder"
              >
                {uploadMut.isPending ? (
                  <Spinner className="size-4" />
                ) : (
                  <UploadCloud size={16} />
                )}
              </Button>
            </TooltipTrigger>
            <TooltipContent>Upload to this folder</TooltipContent>
          </Tooltip>
          {!lessonId && (
            <Button className="drive-new-folder-btn" onClick={handleNewFolder}>
              <Plus size={16} />
              {lessonId ? "New lesson" : "New module"}
            </Button>
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
              return (
                <div
                  key={d.id}
                  className="drive-card doc"
                  draggable
                  onDragStart={(e) => onDocDragStart(e, d.id)}
                  onClick={() => navigate({ to: "/documents/$docId", params: { docId: d.id } })}
                >
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button
                        variant="outline"
                        size="icon-xs"
                        className="drive-card-menu"
                        aria-label="Document actions"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <MoreVertical size={14} />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="start" onClick={(e) => e.stopPropagation()}>
                      <DropdownMenuItem onClick={() => setMovingDoc(d)}>
                        <FolderInput size={13} />
                        Move to…
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        variant="destructive"
                        onClick={() =>
                          setDeleteTarget({ kind: "doc", id: d.id, title: d.filename })
                        }
                      >
                        <Trash2 size={13} />
                        Delete
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
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

      {/* Destructive confirm for folder / document deletes */}
      <AlertDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete "{deleteTarget?.title}"?</AlertDialogTitle>
            <AlertDialogDescription>
              {deleteTarget?.kind === "doc"
                ? "This cannot be undone."
                : "Documents inside will be moved to Unfiled."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-white hover:bg-destructive/90"
              onClick={confirmDelete}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
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
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="new-folder-modal sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <FolderPlus size={20} className="text-muted-foreground" />
            {label}
          </DialogTitle>
          <DialogDescription>
            {isLesson && parentTitle ? `in ${parentTitle}` : "\u00A0"}
          </DialogDescription>
        </DialogHeader>
        <Input
          autoFocus
          type="text"
          className="new-folder-input"
          placeholder={placeholder}
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") handleCreate();
          }}
        />
        {!isLesson && (
          <>
            <div className="form-row grid grid-cols-2 gap-3">
              <div className="grid gap-1.5">
                <Label className="form-label">Term</Label>
                <Select value={term || "none"} onValueChange={(v) => setTerm(v === "none" ? "" : v)}>
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">—</SelectItem>
                    <SelectItem value="Autumn">Autumn</SelectItem>
                    <SelectItem value="Spring">Spring</SelectItem>
                    <SelectItem value="Summer">Summer</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="grid gap-1.5">
                <Label className="form-label">Academic year</Label>
                <Select
                  value={academicYear || "none"}
                  onValueChange={(v) => setAcademicYear(v === "none" ? "" : v)}
                >
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">—</SelectItem>
                    {yearOptions.map((y) => (
                      <SelectItem key={y} value={y}>
                        {y}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="grid gap-1.5">
              <Label className="form-label">
                Exam date (optional — paces the study plan)
              </Label>
              <Input
                type="date"
                value={examDate}
                onChange={(e) => setExamDate(e.target.value)}
              />
            </div>
          </>
        )}
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={handleCreate} disabled={!title.trim() || isPending}>
            {isPending ? (
              <>
                <Spinner className="size-3.5" />
                Creating…
              </>
            ) : (
              "Create"
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
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
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="move-to-modal sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Move "{doc.filename}"</DialogTitle>
          <DialogDescription>Choose a destination</DialogDescription>
        </DialogHeader>
        <ScrollArea className="move-to-list -mx-2 max-h-[50vh] px-2">
          <Button
            variant="ghost"
            className="move-to-option w-full justify-start gap-2 px-2.5 py-2 text-sm font-normal whitespace-normal"
            onClick={() => onMove({ lessonId: null, moduleId: null })}
          >
            <FolderInput size={15} className="text-muted-foreground" />
            Unfiled
          </Button>
          {tree.modules.map((m: Module) => (
            <div key={m.id} className="move-to-group">
              <Button
                variant="ghost"
                className="move-to-option module-root w-full justify-start gap-2 px-2.5 py-2 text-sm font-normal whitespace-normal"
                onClick={() => onMove({ moduleId: m.id })}
              >
                <Folder size={15} className="text-muted-foreground" />
                {m.title}
                <span className="move-to-module-label">module root</span>
              </Button>
              {m.lessons.map((l: Lesson) => (
                <Button
                  key={l.id}
                  variant="ghost"
                  className="move-to-option sub w-full justify-start gap-2 px-2.5 py-2 pl-6 text-sm font-normal whitespace-normal"
                  onClick={() => onMove({ lessonId: l.id })}
                >
                  <FolderOpen size={14} className="text-muted-foreground" />
                  {l.title}
                </Button>
              ))}
              {m.lessons.length === 0 && (
                <div className="move-to-empty">No lessons</div>
              )}
            </div>
          ))}
        </ScrollArea>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
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
          <Collapsible
            key={g.key}
            open={expanded}
            onOpenChange={(open) => onToggle(g.key, open)}
            className="semester-group"
          >
            <CollapsibleTrigger
              className={`semester-group-header ${isCurrent ? "current" : ""}`}
            >
              <ChevronDown
                size={14}
                className="semester-group-chevron transition-transform group-data-[state=closed]/collapsible:-rotate-90"
              />
              <span className="semester-group-label">{g.label}</span>
              {isCurrent && (
                <span className="semester-group-tag">This semester</span>
              )}
              <span className="semester-group-count">
                {g.folders.length} module{g.folders.length === 1 ? "" : "s"}
              </span>
            </CollapsibleTrigger>
            <CollapsibleContent>
              <div className="drive-grid">{g.folders.map(renderCard)}</div>
            </CollapsibleContent>
          </Collapsible>
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
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Module details</DialogTitle>
          <DialogDescription>
            Semester and exam date pace the study plan
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-1.5">
          <Label className="form-label">Title</Label>
          <Input
            autoFocus
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
        </div>
        <div className="form-row grid grid-cols-2 gap-3">
          <div className="grid gap-1.5">
            <Label className="form-label">Term</Label>
            <Select value={term || "none"} onValueChange={(v) => setTerm(v === "none" ? "" : v)}>
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="none">—</SelectItem>
                <SelectItem value="Autumn">Autumn</SelectItem>
                <SelectItem value="Spring">Spring</SelectItem>
                <SelectItem value="Summer">Summer</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="grid gap-1.5">
            <Label className="form-label">Academic year</Label>
            <Select
              value={academicYear || "none"}
              onValueChange={(v) => setAcademicYear(v === "none" ? "" : v)}
            >
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="none">—</SelectItem>
                {yearOptions.map((y) => (
                  <SelectItem key={y} value={y}>
                    {y}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
        <div className="grid gap-1.5">
          <Label className="form-label">Exam date (paces the study plan)</Label>
          <Input
            type="date"
            value={examDate}
            onChange={(e) => setExamDate(e.target.value)}
          />
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button disabled={isPending} onClick={handleSave}>
            {isPending ? "Saving…" : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
