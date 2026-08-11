import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BookOpen,
  FileText,
  UploadCloud,
  X,
  Loader2,
} from "lucide-react";
import * as api from "../api/client";
import { ProfileCard } from "./ProfileCard";

interface Props {
  selectedId: string | null;
  onSelect: (id: string) => void;
}

export function Sidebar({ selectedId, onSelect }: Props) {
  const queryClient = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  const docs = useQuery({ queryKey: ["documents"], queryFn: api.listDocuments });

  const upload = useMutation({
    mutationFn: api.uploadDocument,
    onSuccess: (doc) => {
      queryClient.invalidateQueries({ queryKey: ["documents"] });
      onSelect(doc.id);
    },
  });

  const remove = useMutation({
    mutationFn: api.deleteDocument,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["documents"] }),
  });

  async function handleFiles(files: FileList | null) {
    if (!files || files.length === 0) return;
    await upload.mutateAsync(files[0]);
    if (fileInput.current) fileInput.current.value = "";
  }

  return (
    <aside className="sidebar">
      <div className="sidebar-header">
        <div className="brand-mark">
          <BookOpen size={20} strokeWidth={2.2} />
        </div>
        <div className="brand-text">
          <h1>Study Studio</h1>
          <p>AI-powered notes, quizzes & flashcards</p>
        </div>
      </div>

      <div className="doc-list">
        {docs.isLoading && (
          <div className="loading">
            <Loader2 size={16} className="spinner" />
            Loading…
          </div>
        )}
        {docs.data?.map((doc) => (
          <div
            key={doc.id}
            className={`doc-item ${doc.id === selectedId ? "active" : ""}`}
            onClick={() => onSelect(doc.id)}
          >
            <span className="doc-icon">
              <FileText size={17} />
            </span>
            <div className="meta">
              <span className="filename">{doc.filename}</span>
              <span className="sub">
                {doc.page_count > 1
                  ? `${doc.page_count} pages`
                  : `${(doc.char_count / 1000).toFixed(1)}k chars`}
              </span>
            </div>
            <button
              className="delete-btn ghost"
              onClick={(e) => {
                e.stopPropagation();
                remove.mutate(doc.id);
              }}
              aria-label="Delete document"
            >
              <X size={15} />
            </button>
          </div>
        ))}
        {docs.data?.length === 0 && !docs.isLoading && (
          <div className="empty">No documents yet</div>
        )}
      </div>

      <ProfileCard />

      <div className="upload">
        <input
          ref={fileInput}
          type="file"
          accept=".pdf,.txt,.md"
          style={{ display: "none" }}
          onChange={(e) => handleFiles(e.target.files)}
        />
        <div
          className={`dropzone ${dragging ? "dragging" : ""}`}
          onClick={() => fileInput.current?.click()}
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
              Uploading…
            </>
          ) : (
            <>
              <UploadCloud size={24} className="dz-icon" />
              <span>
                Drop a file or <strong>click to upload</strong>
              </span>
              <span style={{ fontSize: "0.72rem", color: "var(--text-faint)" }}>
                PDF, TXT, or MD
              </span>
            </>
          )}
        </div>
        {upload.isError && (
          <div className="error">
            Upload failed: {(upload.error as Error).message}
          </div>
        )}
      </div>
    </aside>
  );
}
