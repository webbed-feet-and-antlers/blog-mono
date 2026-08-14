import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { Loader2, FileText, Trash2, ChevronRight } from "lucide-react";
import * as api from "../api/client";
import { track } from "../api/track";
import type { ContentItem, TaskType } from "../types";

interface Props {
  type: TaskType;
  title: string;
  emptyMessage: string;
}

/**
 * Shared listing page for a content type (quiz or flashcards) across all
 * documents. Mirrors the ConceptsPage layout: header with summary, a list of
 * cards, and empty/loading states. Clicking a card opens the source document
 * at the right tab.
 */
export function ContentListPage({ type, title, emptyMessage }: Props) {
  const queryClient = useQueryClient();
  const [confirmId, setConfirmId] = useState<string | null>(null);

  const items = useQuery({
    queryKey: ["content-global", type],
    queryFn: () => api.listContent(undefined, type),
  });

  const deleteMut = useMutation({
    mutationFn: (id: string) => api.deleteContent(id),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["content-global", type] }),
  });

  if (items.isLoading) {
    return (
      <div className="loading content-list-loading">
        <Loader2 size={18} className="spinner" />
        Loading {title.toLowerCase()}…
      </div>
    );
  }

  if (!items.data || items.data.length === 0) {
    return (
      <div className="empty content-list-empty">{emptyMessage}</div>
    );
  }

  const all = items.data;

  return (
    <div className="content-list-page">
      <div className="content-list-header">
        <h1>{title}</h1>
        <div className="content-list-summary">
          {all.length} {all.length === 1 ? "deck" : "decks"} across{" "}
          {new Set(all.map((i) => i.document_id)).size}{" "}
          {new Set(all.map((i) => i.document_id)).size === 1
            ? "document"
            : "documents"}
        </div>
      </div>

      <div className="content-list-grid">
        {all.map((item) => (
          <ContentCard
            key={item.id}
            item={item}
            type={type}
            onDelete={() => setConfirmId(item.id)}
          />
        ))}
      </div>

      {/* Delete confirmation */}
      {confirmId && (
        <div className="modal-backdrop" onClick={() => setConfirmId(null)}>
          <div
            className="modal-content"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="modal-header">
              <Trash2 size={20} className="text-warning" />
              <h3>Delete this {type === "quiz" ? "quiz" : "deck"}?</h3>
            </div>
            <p>This cannot be undone.</p>
            <div className="modal-actions">
              <button
                type="button"
                className="ghost"
                onClick={() => setConfirmId(null)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="primary danger-btn"
                onClick={() => {
                  deleteMut.mutate(confirmId);
                  setConfirmId(null);
                }}
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function ContentCard({
  item,
  type,
  onDelete,
}: {
  item: ContentItem;
  type: TaskType;
  onDelete: () => void;
}) {
  const navigate = useNavigate();
  const docName = item.document?.filename ?? "Unknown document";
  const docTopic = item.document?.topic;

  // Extract count from content (questions for quiz, cards for flashcards).
  let count = 0;
  let countLabel = "";
  if (type === "quiz") {
    const c = item.content as { questions?: unknown[] };
    count = c.questions?.length ?? 0;
    countLabel = count === 1 ? "question" : "questions";
  } else if (type === "flashcards") {
    const c = item.content as { cards?: unknown[] };
    count = c.cards?.length ?? 0;
    countLabel = count === 1 ? "card" : "cards";
  }

  const deckTitle =
    (item.content as { title?: string }).title ?? "Untitled";

  return (
    <div
      className="content-card drive-card"
      onClick={() => {
        track("content.opened", { content_id: item.id, type: item.type });
        navigate({
          to: "/documents/$docId/$tab",
          params: { docId: item.document_id, tab: type },
        });
      }}
    >
      <button
        type="button"
        className="drive-card-menu"
        title={`Delete ${type}`}
        onClick={(e) => {
          e.stopPropagation();
          onDelete();
        }}
      >
        <Trash2 size={14} />
      </button>
      <div className="drive-card-icon doc-icon">
        <FileText size={24} />
      </div>
      <div className="drive-card-title">{deckTitle}</div>
      <div className="drive-card-subtitle">
        {count} {countLabel}
      </div>
      <div className="content-card-doc">
        <ChevronRight size={11} />
        <span title={docName}>{docName}</span>
      </div>
      {docTopic && <div className="content-card-topic">{docTopic}</div>}
    </div>
  );
}
