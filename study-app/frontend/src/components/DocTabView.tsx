import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useRouterState } from "@tanstack/react-router";
import {
  FileText,
  CircleHelp,
  Layers,
  Sparkles,
  Loader2,
  Trash2,
  FileUp,
  Wand2,
  ArrowRight,
  Network,
} from "lucide-react";
import * as api from "../api/client";
import type { ContentItem, TaskType, TabId } from "../types";
import { NotesView } from "./NotesView";
import { QuizView } from "./QuizView";
import { FlashcardView } from "./FlashcardView";
import { DocumentView } from "./DocumentView";
import { ConceptListView } from "./ConceptListView";

// Module-level flag set by the recommendation panel to signal that generation
// should auto-trigger when DocTabView mounts. Cleaner than a URL search param.
export const pendingGenerate = { value: false };

const TABS: { id: TabId; label: string; icon: typeof FileText }[] = [
  { id: "document", label: "Document", icon: FileText },
  { id: "notes", label: "Notes", icon: FileText },
  { id: "quiz", label: "Quiz", icon: CircleHelp },
  { id: "flashcards", label: "Flashcards", icon: Layers },
  { id: "concepts", label: "Concepts", icon: Network },
];

export function DocTabView() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const routeParams = useRouterState({ select: (s) => s.location.pathname });
  // Extract docId and tab from the URL path: /documents/$docId/$tab
  const parts = routeParams.split("/");
  const docId = parts[2] ?? "";
  const tab = (parts[3] as TabId) ?? "document";

  const [hint, setHint] = useState("");
  const [generating, setGenerating] = useState(false);
  const [genStatus, setGenStatus] = useState<string | null>(null);
  const [genError, setGenError] = useState<string | null>(null);

  // Auto-trigger generation when navigated from a recommendation.
  useEffect(() => {
    if (pendingGenerate.value && !generating && tab !== "document") {
      pendingGenerate.value = false;
      handleGenerate();
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const doc = useQuery({
    queryKey: ["document", docId],
    queryFn: () => api.getDocument(docId),
  });

  const content = useQuery({
    queryKey: ["content", docId, tab],
    queryFn: () => api.listContent(docId, tab as TaskType),
    enabled: tab !== "document" && tab !== "concepts",
  });

  const proactiveDecks = useQuery({
    queryKey: ["proactive-decks"],
    queryFn: api.listProactiveDecks,
    refetchInterval: 15000,
  });
  const docProactiveDeck = proactiveDecks.data?.find(
    (d) => d.document_id === docId,
  );

  async function handleGenerate() {
    if (!docId || tab === "document" || tab === "concepts") return;
    // Guard: don't generate from an audio doc that hasn't been transcribed yet.
    if (doc.data?.transcription_status === "pending" || doc.data?.transcription_status === "transcribing") {
      setGenError("Transcription still in progress — please wait for it to complete.");
      return;
    }
    setGenerating(true);
    setGenStatus("Reading the document…");
    setGenError(null);

    await api.generateStream(
      {
        document_id: docId,
        task_type: tab as TaskType,
        instructions: hint.trim() || undefined,
      },
      {
        onStatus: (status) => setGenStatus(status),
        onDone: () => {
          queryClient.invalidateQueries({ queryKey: ["content", docId, tab] });
          queryClient.invalidateQueries({ queryKey: ["memory"] });
          queryClient.invalidateQueries({ queryKey: ["learner-profile"] });
          queryClient.invalidateQueries({ queryKey: ["proactive-decks"] });
          queryClient.invalidateQueries({ queryKey: ["recommend"] });
        },
        onError: (message) => setGenError(message),
      },
    );

    setGenerating(false);
    setGenStatus(null);
  }

  const removeContent = useMutation({
    mutationFn: (id: string) => api.deleteContent(id),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["content", docId, tab] }),
  });

  const items: ContentItem[] = content.data ?? [];
  const latest = items[0];

  return (
    <>
      <div className="section-head">
        <div className="doc-title-icon">
          <FileText size={20} strokeWidth={1.8} />
        </div>
        <div>
          <h2>{doc.data?.filename ?? "Loading…"}</h2>
          {doc.data?.topic && (
            <p className="doc-topic-subtitle">{doc.data.topic}</p>
          )}
        </div>
      </div>

      {/* Proactive banner */}
      {docProactiveDeck && (
        <ProactiveBanner
          deck={docProactiveDeck}
          onView={() =>
            navigate({
              to: "/documents/$docId/$tab",
              params: { docId, tab: "flashcards" },
            })
          }
        />
      )}

      <div className="tabs">
        {TABS.map((t) => {
          const Icon = t.icon;
          return (
            <button
              key={t.id}
              className={`tab ${tab === t.id ? "active" : ""}`}
              onClick={() =>
                navigate({
                  to: "/documents/$docId/$tab",
                  params: { docId, tab: t.id },
                })
              }
            >
              <Icon size={16} />
              {t.label}
            </button>
          );
        })}
      </div>

      {/* Document tab: show source text */}
      {tab === "document" && doc.data && <DocumentView doc={doc.data} />}

      {/* Concepts tab: knowledge graph + mastery */}
      {tab === "concepts" && <ConceptListView />}

      {/* AI-generation tabs */}
      {tab !== "document" && tab !== "concepts" && (
        <>
          <div className="generate-bar">
            <input
              className="hint-input"
              placeholder={
                tab === "quiz"
                  ? "Hint: focus on chapter 3, 10 questions"
                  : tab === "notes"
                    ? "Hint: concise bullet points"
                    : "Hint: definition-style cards"
              }
              value={hint}
              onChange={(e) => setHint(e.target.value)}
              disabled={generating}
            />
            <button
              className="primary"
              disabled={generating}
              onClick={() => handleGenerate()}
            >
              {generating ? (
                <>
                  <Loader2 size={16} className="spinner" />
                  Generating…
                </>
              ) : (
                <>
                  <Sparkles size={16} />
                  Generate {tab}
                </>
              )}
            </button>{" "}
            {latest && !generating && (
              <button
                className="danger"
                onClick={() => removeContent.mutate(latest.id)}
              >
                <Trash2 size={15} />
              </button>
            )}
          </div>

          {genError && (
            <div className="error">Generation failed: {genError}</div>
          )}

          {generating && (
            <div className="loading">
              <Loader2 size={18} className="spinner" />
              <div className="gen-status">{genStatus}</div>
            </div>
          )}

          {!generating && latest && <ContentRender item={latest} />}

          {!generating && !latest && !content.isLoading && (
            <div className="empty">
              <FileUp
                size={40}
                strokeWidth={1.4}
                style={{ margin: "0 auto 12px", display: "block", opacity: 0.3 }}
              />
              No {tab} yet. Click <strong>Generate</strong> to have the agent
              create some.
            </div>
          )}
        </>
      )}
    </>
  );
}

function ProactiveBanner({
  deck,
  onView,
}: {
  deck: ContentItem;
  onView: () => void;
}) {
  const title = (deck.content as any)?.title ?? "Review deck";
  const cardCount = (deck.content as any)?.cards?.length ?? 0;
  const seenKey = `proactive-seen-${deck.id}`;
  const [seen, setSeen] = useState(() => {
    try {
      return localStorage.getItem(seenKey) === "1";
    } catch {
      return false;
    }
  });
  if (seen) return null;
  return (
    <div className="proactive-banner">
      <div className="pb-icon">
        <Wand2 size={20} />
      </div>
      <div className="pb-text">
        <div className="pb-title">
          <Sparkles size={14} />
          Agent prepared a review deck for you
        </div>
        <div className="pb-sub">
          {title} · {cardCount} cards targeting your weak areas
        </div>
      </div>
      <button
        className="primary"
        onClick={() => {
          try {
            localStorage.setItem(seenKey, "1");
          } catch {
            /* ignore */
          }
          setSeen(true);
          onView();
        }}
      >
        Review now
        <ArrowRight size={15} />
      </button>
    </div>
  );
}

function ContentRender({ item }: { item: ContentItem }) {
  if (item.type === "notes") {
    return <NotesView content={item.content as any} />;
  }
  if (item.type === "quiz") {
    return <QuizView contentId={item.id} content={item.content as any} />;
  }
  return <FlashcardView contentId={item.id} content={item.content as any} />;
}
