import { useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  BookOpen,
  Home,
  LayoutGrid,
  Network,
  Mic,
  UploadCloud,
  Loader2,
  CircleHelp,
  Layers,
} from "lucide-react";
import * as api from "../api/client";
import { track } from "../api/track";
import { FileToModuleModal } from "./FileToModuleModal";
import { ProfileCard } from "./ProfileCard";

interface Props {
  onHome: () => void;
  onRecord: () => void;
  onConcepts: () => void;
  onDrive: () => void;
  onQuizzes: () => void;
  onFlashcards: () => void;
  onNavigate: (id: string) => void;
}

/**
 * Slim navigation sidebar. Organization (folders, moving, renaming, uploading
 * into specific folders) lives in the Drive page; the sidebar is just for
 * getting around quickly.
 */
export function Sidebar({
  onHome,
  onRecord,
  onConcepts,
  onDrive,
  onQuizzes,
  onFlashcards,
  onNavigate,
}: Props) {
  const queryClient = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);

  const upload = useMutation({
    mutationFn: ({
      file,
      target,
    }: {
      file: File;
      target?: { lessonId?: string; moduleId?: string };
    }) =>
      api.uploadDocument(
        file,
        target?.lessonId,
        (pct) => setUploadProgress(pct),
        target?.moduleId,
      ),
    onSuccess: (doc) => {
      setUploadProgress(null);
      queryClient.invalidateQueries({ queryKey: ["documents"] });
      queryClient.invalidateQueries({ queryKey: ["module-tree"] });
      onNavigate(doc.id);
    },
    onError: () => setUploadProgress(null),
  });

  // A picked file awaiting a module choice (the "add to module" prompt).
  const [pendingFile, setPendingFile] = useState<File | null>(null);

  async function handleFiles(files: FileList | null) {
    if (!files || files.length === 0) return;
    // Prompt for a module (current semester first) instead of filing
    // straight to Unfiled.
    setPendingFile(files[0]);
    if (fileInput.current) fileInput.current.value = "";
  }

  async function uploadWith(target: {
    moduleId?: string;
    lessonId?: string;
  } | null) {
    const file = pendingFile;
    setPendingFile(null);
    if (!file) return;
    await upload.mutateAsync({ file, target: target ?? undefined });
  }

  const uploading = upload.isPending;

  return (
    <aside className="sidebar">
      {/* Brand */}
      <div className="sidebar-header">
        <div className="brand-clickable" onClick={onHome} title="Home">
          <div className="brand-mark">
            <BookOpen size={20} strokeWidth={2.2} />
          </div>
          <div className="brand-text">
            <h1>Study Studio</h1>
            <p>AI-powered notes, quizzes & flashcards</p>
          </div>
        </div>
      </div>

      {/* Navigation */}
      <nav className="sidebar-nav">
        <button type="button" className="sidebar-nav-btn" onClick={() => { track("navigation.moved", { to: "home" }); onHome(); }}>
          <Home size={16} />
          Home
        </button>
        <button type="button" className="sidebar-nav-btn" onClick={() => { track("navigation.moved", { to: "modules" }); onDrive(); }}>
          <LayoutGrid size={16} />
          Modules
        </button>
        <button type="button" className="sidebar-nav-btn" onClick={() => { track("navigation.moved", { to: "concepts" }); onConcepts(); }}>
          <Network size={16} />
          Concepts
        </button>
        <button type="button" className="sidebar-nav-btn" onClick={() => { track("navigation.moved", { to: "quizzes" }); onQuizzes(); }}>
          <CircleHelp size={16} />
          Quizzes
        </button>
        <button type="button" className="sidebar-nav-btn" onClick={() => { track("navigation.moved", { to: "flashcards" }); onFlashcards(); }}>
          <Layers size={16} />
          Flashcards
        </button>
        <button
          type="button"
          className="sidebar-nav-btn"
          onClick={() => { track("navigation.moved", { to: "record" }); onRecord(); }}
          disabled={uploading}
        >
          <Mic size={16} />
          Record lecture
        </button>
        <button
          type="button"
          className="sidebar-nav-btn"
          onClick={() => !uploading && fileInput.current?.click()}
          disabled={uploading}
        >
          {uploading ? (
            <Loader2 size={16} className="spinner" />
          ) : (
            <UploadCloud size={16} />
          )}
          {uploading
            ? uploadProgress !== null
              ? `Uploading ${uploadProgress}%`
              : "Uploading…"
            : "Upload"}
        </button>
        {upload.isError && (
          <div className="sidebar-upload-error">
            Upload failed: {(upload.error as Error).message}
          </div>
        )}
      </nav>

      <input
        ref={fileInput}
        type="file"
        accept=".pdf,.txt,.md,.pptx,.docx,.xlsx,.doc,.ppt,.xls,.webm,.mp3,.m4a,.wav,.ogg"
        style={{ display: "none" }}
        onChange={(e) => handleFiles(e.target.files)}
      />

      {/* Spacer pushes profile to the bottom */}
      <div className="sidebar-spacer" />

      <ProfileCard />

      {pendingFile && (
        <FileToModuleModal noun="document" onSelect={uploadWith} />
      )}
    </aside>
  );
}
