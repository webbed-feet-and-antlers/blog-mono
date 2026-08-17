import { useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  BookOpen,
  Home,
  LayoutGrid,
  Network,
  Mic,
  UploadCloud,
  CircleHelp,
  Layers,
} from "lucide-react";
import * as api from "../api/client";
import { track } from "../api/track";
import { toast } from "sonner";
import { FileToModuleModal } from "./FileToModuleModal";
import { ProfileCard } from "./ProfileCard";
import {
  Sidebar as SidebarRoot,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar";
import { Spinner } from "@/components/ui/spinner";

interface Props {
  onHome: () => void;
  onRecord: () => void;
  onConcepts: () => void;
  onDrive: () => void;
  onQuizzes: () => void;
  onFlashcards: () => void;
  onNavigate: (id: string) => void;
  /** Current pathname, for marking the active nav item. */
  pathname: string;
}

/**
 * App navigation sidebar built on the shadcn Sidebar primitive: collapsible
 * on desktop, a slide-over sheet on mobile. Organization (folders, moving,
 * renaming) lives in the Drive page; the sidebar is just for getting around
 * quickly.
 */
export function AppSidebar({
  onHome,
  onRecord,
  onConcepts,
  onDrive,
  onQuizzes,
  onFlashcards,
  onNavigate,
  pathname,
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
      toast.success("Document uploaded", { description: doc.filename });
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

  const navItems: {
    label: string;
    icon: typeof Home;
    path: string;
    action: () => void;
  }[] = [
    {
      label: "Home",
      icon: Home,
      path: "/",
      action: () => {
        track("navigation.moved", { to: "home" });
        onHome();
      },
    },
    {
      label: "Modules",
      icon: LayoutGrid,
      path: "/modules",
      action: () => {
        track("navigation.moved", { to: "modules" });
        onDrive();
      },
    },
    {
      label: "Concepts",
      icon: Network,
      path: "/concepts",
      action: () => {
        track("navigation.moved", { to: "concepts" });
        onConcepts();
      },
    },
    {
      label: "Quizzes",
      icon: CircleHelp,
      path: "/quizzes",
      action: () => {
        track("navigation.moved", { to: "quizzes" });
        onQuizzes();
      },
    },
    {
      label: "Flashcards",
      icon: Layers,
      path: "/flashcards",
      action: () => {
        track("navigation.moved", { to: "flashcards" });
        onFlashcards();
      },
    },
  ];

  return (
    <SidebarRoot>
      {/* Brand */}
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton
              size="lg"
              className="brand-clickable"
              onClick={onHome}
              aria-label="Study Studio — home"
            >
              <div className="brand-mark">
                <BookOpen size={20} strokeWidth={2.2} />
              </div>
              <div className="brand-text min-w-0">
                <h1 className="truncate text-base font-semibold">Study Studio</h1>
                <p className="truncate text-xs text-muted-foreground">
                  AI-powered notes, quizzes & flashcards
                </p>
              </div>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>

      {/* Navigation */}
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupContent>
            <SidebarMenu>
              {navItems.map((item) => (
                <SidebarMenuItem key={item.label}>
                  <SidebarMenuButton
                    isActive={pathname === item.path}
                    tooltip={item.label}
                    onClick={item.action}
                  >
                    <item.icon size={16} />
                    <span>{item.label}</span>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
              <SidebarMenuItem>
                <SidebarMenuButton
                  tooltip="Record lecture"
                  onClick={() => {
                    track("navigation.moved", { to: "record" });
                    onRecord();
                  }}
                  disabled={uploading}
                >
                  <Mic size={16} />
                  <span>Record lecture</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton
                  tooltip="Upload a document"
                  onClick={() => !uploading && fileInput.current?.click()}
                  disabled={uploading}
                >
                  {uploading ? (
                    <Spinner className="size-4" />
                  ) : (
                    <UploadCloud size={16} />
                  )}
                  <span>
                    {uploading
                      ? uploadProgress !== null
                        ? `Uploading ${uploadProgress}%`
                        : "Uploading…"
                      : "Upload"}
                  </span>
                </SidebarMenuButton>
              </SidebarMenuItem>
              {upload.isError && (
                <div className="sidebar-upload-error">
                  Upload failed: {(upload.error as Error).message}
                </div>
              )}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>

      <input
        ref={fileInput}
        type="file"
        accept=".pdf,.txt,.md,.pptx,.docx,.xlsx,.doc,.ppt,.xls,.webm,.mp3,.m4a,.wav,.ogg"
        style={{ display: "none" }}
        onChange={(e) => handleFiles(e.target.files)}
      />

      {/* Profile pinned to the bottom */}
      <SidebarFooter>
        <ProfileCard />
      </SidebarFooter>

      {pendingFile && (
        <FileToModuleModal noun="document" onSelect={uploadWith} />
      )}
    </SidebarRoot>
  );
}
