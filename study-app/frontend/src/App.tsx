import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
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
  Eye,
} from "lucide-react";
import * as api from "./api/client";
import type { ContentItem, TaskType } from "./types";
import { Sidebar } from "./components/Sidebar";
import { NotesView } from "./components/NotesView";
import { QuizView } from "./components/QuizView";
import { FlashcardView } from "./components/FlashcardView";
import { DocumentView } from "./components/DocumentView";
import { RecommendationPanel } from "./components/RecommendationPanel";

type TabId = TaskType | "document";

const TABS: { id: TabId; label: string; icon: typeof FileText }[] = [
  { id: "document", label: "Document", icon: Eye },
  { id: "notes", label: "Notes", icon: FileText },
  { id: "quiz", label: "Quiz", icon: CircleHelp },
  { id: "flashcards", label: "Flashcards", icon: Layers },
];

export default function App() {
  const queryClient = useQueryClient();
  const [selectedDocId, setSelectedDocId] = useState<string | null>(null);
  const [tab, setTab] = useState<TabId>("document");
  const [hint, setHint] = useState("");

  const doc = useQuery({
    queryKey: ["document", selectedDocId],
    queryFn: () => api.getDocument(selectedDocId!),
    enabled: !!selectedDocId,
  });

  const content = useQuery({
    queryKey: ["content", selectedDocId, tab],
    queryFn: () => api.listContent(selectedDocId!, tab as TaskType),
    enabled: !!selectedDocId && tab !== "document",
  });

  // Proactive decks the agent generated on its own (for the banner).
  const proactiveDecks = useQuery({
    queryKey: ["proactive-decks"],
    queryFn: api.listProactiveDecks,
    refetchInterval: 15000, // poll so newly-generated decks appear live
  });
  const docProactiveDeck = proactiveDecks.data?.find(
    (d) => d.document_id === selectedDocId,
  );

  const [generating, setGenerating] = useState(false);
  const [genStatus, setGenStatus] = useState<string | null>(null);
  const [genError, setGenError] = useState<string | null>(null);

  async function handleGenerate(
    overrideDocId?: string,
    overrideTaskType?: string,
  ) {
    const docId = overrideDocId || selectedDocId;
    const taskType = (overrideTaskType || tab) as TaskType;
    if (!docId) return;

    // If generating from a recommendation, navigate to that doc + tab first.
    if (overrideDocId && overrideDocId !== selectedDocId) {
      setSelectedDocId(overrideDocId);
    }
    if (overrideTaskType) {
      setTab(overrideTaskType as TabId);
    }

    setGenerating(true);
    setGenStatus("Reading the document…");
    setGenError(null);

    await api.generateStream(
      {
        document_id: docId,
        task_type: taskType,
        instructions: hint.trim() || undefined,
      },
      {
        onStatus: (status) => setGenStatus(status),
        onDone: () => {
          queryClient.invalidateQueries({
            queryKey: ["content", docId, taskType],
          });
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
      queryClient.invalidateQueries({ queryKey: ["content", selectedDocId, tab] }),
  });

  const items: ContentItem[] = content.data ?? [];
  const latest = items[0]; // list is ordered newest-first

  return (
    <div className="app">
      <Sidebar selectedId={selectedDocId} onSelect={setSelectedDocId} />

      <main className="main">
        {!selectedDocId && !generating && (
          <RecommendationPanel
            onSelect={setSelectedDocId}
            onTab={(t) => setTab(t as TabId)}
            onGenerate={(docId, taskType) => handleGenerate(docId, taskType)}
          />
        )}

        {selectedDocId && (
          <>
            <div className="section-head">
              <div className="doc-title-icon">
                <FileText size={20} strokeWidth={1.8} />
              </div>
              <h2>{doc.data?.filename ?? "Loading…"}</h2>
            </div>

            {/* Proactive banner: the agent prepared something for you */}
            {docProactiveDeck && (
              <ProactiveBanner
                deck={docProactiveDeck}
                onView={() => setTab("flashcards")}
              />
            )}

            <div className="tabs">
              {TABS.map((t) => {
                const Icon = t.icon;
                return (
                  <button
                    key={t.id}
                    className={`tab ${tab === t.id ? "active" : ""}`}
                    onClick={() => setTab(t.id)}
                  >
                    <Icon size={16} />
                    {t.label}
                  </button>
                );
              })}
            </div>

            {/* Document tab: show the original source text */}
            {tab === "document" && doc.data && (
              <DocumentView doc={doc.data} />
            )}

            {/* AI-generation tabs: generate controls + content */}
            {tab !== "document" && (
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
                  <div className="error">
                    Generation failed: {genError}
                  </div>
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
        )}
      </main>
    </div>
  );
}

function ProactiveBanner({
  deck,
  onView,
}: {
  deck: ContentItem;
  onView: () => void;
}) {
  const title =
    (deck.content as any)?.title ?? "Review deck";
  const cardCount = (deck.content as any)?.cards?.length ?? 0;
  // Track in localStorage so the banner doesn't nag after the user has seen it.
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
