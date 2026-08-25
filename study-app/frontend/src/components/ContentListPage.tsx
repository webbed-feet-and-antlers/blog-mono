import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { FileText, Trash2, ChevronRight } from "lucide-react";
import * as api from "../api/client";
import { track } from "../api/track";
import { toast } from "sonner";
import type { ContentItem, TaskType } from "../types";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
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
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

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
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["content-global", type] });
      toast.success(`${type === "quiz" ? "Quiz" : "Deck"} deleted`);
    },
  });

  if (items.isLoading) {
    return (
      <div className="loading content-list-loading">
        <Spinner className="size-[18px]" />
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
      <AlertDialog
        open={confirmId !== null}
        onOpenChange={(open) => !open && setConfirmId(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle className="flex-left flex items-center gap-2">
              <Trash2 size={20} className="text-warn" />
              Delete this {type === "quiz" ? "quiz" : "deck"}?
            </AlertDialogTitle>
            <AlertDialogDescription>
              This cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel onClick={() => setConfirmId(null)}>
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-white hover:bg-destructive/90"
              onClick={() => {
                if (confirmId) deleteMut.mutate(confirmId);
                setConfirmId(null);
              }}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
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
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            variant="outline"
            size="icon-xs"
            className="drive-card-menu"
            aria-label={`Delete ${type}`}
            onClick={(e) => {
              e.stopPropagation();
              onDelete();
            }}
          >
            <Trash2 size={14} />
          </Button>
        </TooltipTrigger>
        <TooltipContent>Delete {type}</TooltipContent>
      </Tooltip>
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
