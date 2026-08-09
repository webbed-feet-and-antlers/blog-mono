import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as api from "../api/client";

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
        <h1>📚 Study App</h1>
        <p>Upload a document, then generate notes, quizzes, or flashcards.</p>
      </div>

      <div className="doc-list">
        {docs.isLoading && <div className="loading">Loading…</div>}
        {docs.data?.map((doc) => (
          <div
            key={doc.id}
            className={`doc-item ${doc.id === selectedId ? "active" : ""}`}
            onClick={() => onSelect(doc.id)}
          >
            <div className="meta">
              <span className="filename">{doc.filename}</span>
              <span className="sub">
                {doc.page_count > 1
                  ? `${doc.page_count} pages`
                  : `${(doc.char_count / 1000).toFixed(1)}k chars`}
              </span>
            </div>
            <button
              className="small danger"
              onClick={(e) => {
                e.stopPropagation();
                remove.mutate(doc.id);
              }}
            >
              ✕
            </button>
          </div>
        ))}
        {docs.data?.length === 0 && (
          <div className="empty">No documents yet.</div>
        )}
      </div>

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
          {upload.isPending
            ? "Uploading…"
            : "📎 Drop a PDF / TXT / MD or click to upload"}
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
