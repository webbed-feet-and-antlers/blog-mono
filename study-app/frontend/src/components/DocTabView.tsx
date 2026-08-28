import { useEffect, useRef, useState } from "react";
import { useUser } from "@clerk/clerk-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useRouterState } from "@tanstack/react-router";
import {
  FileText,
  CircleHelp,
  Layers,
  Sparkles,
  Trash2,
  FileUp,
  Wand2,
  ArrowRight,
  Network,
} from "lucide-react";
import * as api from "../api/client";
import { track } from "../api/track";
import { toast } from "sonner";
import type { ContentItem, TaskType, TabId } from "../types";
import { NotesView } from "./NotesView";
import { QuizView } from "./QuizView";
import { FlashcardView } from "./FlashcardView";
import { DocumentView } from "./DocumentView";
import { ConceptListView } from "./ConceptListView";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Spinner } from "@/components/ui/spinner";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

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
  // Which tab is currently generating (null = none). Generation is scoped
  // per tab so the loader only shows on the tab that started it: switching
  // tabs mid-generation keeps the stream running in the background while
  // other tabs render their content normally; returning to the generating
  // tab shows its loader again until the stream finishes.
  const [generatingTab, setGeneratingTab] = useState<TaskType | null>(null);
  const [genStatus, setGenStatus] = useState<string | null>(null);
  const [genError, setGenError] = useState<{ tab: string; message: string } | null>(
    null,
  );
  const generating = generatingTab === tab;
  // Which generated item is selected (null = latest). Only relevant when a
  // document has multiple decks/quizzes/notes versions.
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // --- Dwell tracking (in-app actions as agent memory) ---------------------
  // Latest-value refs so the unmount/pagehide emitter reads current context.
  const docIdRef = useRef(docId);
  const tabRef = useRef(tab);
  docIdRef.current = docId;
  tabRef.current = tab;
  const openedAtRef = useRef(Date.now());
  const closedRef = useRef(false);

  function emitDocumentClosed() {
    if (closedRef.current || !docIdRef.current) return;
    closedRef.current = true;
    const dwell_secs = Math.round((Date.now() - openedAtRef.current) / 1000);
    track("document.closed", {
      document_id: docIdRef.current,
      tab: tabRef.current,
      dwell_secs,
    });
  }

  useEffect(() => {
    track("document.opened", { document_id: docId, tab });
    closedRef.current = false;
    openedAtRef.current = Date.now();
    const onPageHide = () => emitDocumentClosed();
    window.addEventListener("pagehide", onPageHide);
    return () => {
      window.removeEventListener("pagehide", onPageHide);
      emitDocumentClosed();
    };
  }, [docId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-trigger generation when navigated from a recommendation.
  useEffect(() => {
    if (pendingGenerate.value && !generating && tab !== "document") {
      pendingGenerate.value = false;
      handleGenerate("recommendation");
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const doc = useQuery({
    queryKey: ["document", docId],
    queryFn: () => api.getDocument(docId),
    // Light polling so background changes surface without a manual refresh
    // (e.g. the agent auto-renaming a machine-generated filename).
    refetchInterval: 15000,
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

  async function handleGenerate(trigger: "user" | "recommendation" = "user") {
    if (!docId || tab === "document" || tab === "concepts") return;
    // Guard: don't generate from an audio doc that hasn't been transcribed yet.
    if (doc.data?.transcription_status === "pending" || doc.data?.transcription_status === "transcribing") {
      setGenError({
        tab,
        message: "Transcription still in progress — please wait for it to complete.",
      });
      return;
    }
    track("generation.requested", {
      document_id: docId,
      task_type: tab,
      hint: hint.trim().slice(0, 200) || null,
      trigger,
    });
    // `tab` is captured in this closure, so the stream below keeps writing
    // to the tab that started it even if the user navigates to another one.
    setGeneratingTab(tab as TaskType);
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
          setSelectedId(null); // show the newly generated (latest) item
          queryClient.invalidateQueries({ queryKey: ["content", docId, tab] });
          queryClient.invalidateQueries({ queryKey: ["memory"] });
          queryClient.invalidateQueries({ queryKey: ["learner-profile"] });
          queryClient.invalidateQueries({ queryKey: ["proactive-decks"] });
          queryClient.invalidateQueries({ queryKey: ["recommend"] });
          toast.success(`${tab === "flashcards" ? "Deck" : tab} generated`);
        },
        onError: (message) => setGenError({ tab, message }),
      },
    );

    setGeneratingTab(null);
    setGenStatus(null);
  }

  const removeContent = useMutation({
    mutationFn: (id: string) => api.deleteContent(id),
    onMutate: (id) =>
      track("content.deleted", { content_id: id, document_id: docId, type: tab }),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["content", docId, tab] }),
  });

  const items: ContentItem[] = content.data ?? [];
  const latest = items[0];
  // Fall back to latest when nothing is selected (or the selection was
  // deleted / belongs to another tab).
  const selected =
    (selectedId ? items.find((i) => i.id === selectedId) : undefined) ??
    latest;

  // Broken link / deleted document: show a proper error state instead of
  // spinning on "Loading…" forever.
  if (doc.isError) {
    return (
      <div className="empty-hero">
        <div className="empty-icon">
          <FileText size={30} strokeWidth={1.8} />
        </div>
        <h2>Document not found</h2>
        <p>It may have been deleted, or the link is broken.</p>
        <Button className="mt-5" onClick={() => navigate({ to: "/modules" })}>
          Back to modules
        </Button>
      </div>
    );
  }

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

      <Tabs
        value={tab}
        onValueChange={(v) => {
          if (v !== tab) {
            track("tab.switched", { document_id: docId, from: tab, to: v });
            navigate({
              to: "/documents/$docId/$tab",
              params: { docId, tab: v as TabId },
            });
          }
        }}
      >
        <TabsList className="tabs">
          {TABS.map((t) => {
            const Icon = t.icon;
            return (
              <TabsTrigger key={t.id} value={t.id} className="gap-1.5">
                <Icon size={16} />
                {t.label}
              </TabsTrigger>
            );
          })}
        </TabsList>
      </Tabs>

      {/* Document tab: show source text */}
      {tab === "document" && doc.data && <DocumentView doc={doc.data} />}

      {/* Concepts tab: knowledge graph + mastery */}
      {tab === "concepts" && <ConceptListView />}

      {/* AI-generation tabs */}
      {tab !== "document" && tab !== "concepts" && (
        <>
          <div className="generate-bar">
            <Input
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
            <Button disabled={generating} onClick={() => handleGenerate()}>
              {generating ? (
                <>
                  <Spinner className="size-4" />
                  Generating…
                </>
              ) : (
                <>
                  <Sparkles size={16} />
                  Generate {tab}
                </>
              )}
            </Button>{" "}
            {selected && !generating && (
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="danger-ghost"
                    size="icon"
                    onClick={() => removeContent.mutate(selected.id)}
                    aria-label="Delete selected"
                  >
                    <Trash2 size={15} />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Delete selected</TooltipContent>
              </Tooltip>
            )}
          </div>

          {genError?.tab === tab && (
            <Alert variant="destructive" className="mb-4">
              <AlertDescription>
                Generation failed: {genError.message}
              </AlertDescription>
            </Alert>
          )}

          {generating && (
            <div className="loading">
              <Spinner className="size-[18px]" />
              <div className="gen-status">{genStatus}</div>
            </div>
          )}

          {/* Version picker — shown when the document has multiple versions
              of this content type (e.g. several flashcard decks). */}
          {!generating && items.length > 1 && (
            <ToggleGroup
              type="single"
              value={selected?.id}
              onValueChange={(v) => {
                const item = items.find((i) => i.id === v);
                if (item) {
                  setSelectedId(item.id);
                  track("deck.version_selected", {
                    document_id: docId,
                    content_id: item.id,
                    type: item.type,
                  });
                }
              }}
              className="deck-picker justify-start gap-2 overflow-x-auto"
            >
              {items.map((item) => {
                const meta = itemMeta(item);
                return (
                  <ToggleGroupItem
                    key={item.id}
                    value={item.id}
                    className="h-auto min-w-[150px] flex-col items-start gap-0.5 rounded-md border px-3.5 py-2 text-left font-normal shadow-none data-[state=on]:border-primary data-[state=on]:bg-accent"
                  >
                    <span className="max-w-[180px] truncate text-[0.8rem] font-semibold data-[state=on]:text-primary">
                      {meta.title}
                    </span>
                    <span className="text-[0.7rem] text-muted-foreground">
                      {meta.count} · {timeAgo(item.created_at)}
                    </span>
                  </ToggleGroupItem>
                );
              })}
            </ToggleGroup>
          )}

          {!generating && selected && <ContentRender item={selected} />}

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
  const { user } = useUser();
  const seenKey = `proactive-seen:${user?.id ?? "anon"}:${deck.id}`;
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
      <Button
        onClick={() => {
          try {
            localStorage.setItem(seenKey, "1");
          } catch {
            /* ignore */
          }
          setSeen(true);
          track("proactive.accepted", { content_id: deck.id });
          onView();
        }}
      >
        Review now
        <ArrowRight size={15} />
      </Button>
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

/** Short label + count for a content item, used by the version picker. */
function itemMeta(item: ContentItem): { title: string; count: string } {
  const c = item.content as { title?: string; questions?: unknown[]; cards?: unknown[] };
  if (item.type === "quiz") {
    return {
      title: c.title ?? "Quiz",
      count: `${c.questions?.length ?? 0} questions`,
    };
  }
  if (item.type === "flashcards") {
    return {
      title: c.title ?? "Deck",
      count: `${c.cards?.length ?? 0} cards`,
    };
  }
  // Notes have no title — distinguish by date.
  return { title: `Notes (${timeAgo(item.created_at)})`, count: "notes" };
}

/** Compact relative time: "5m ago", "3h ago", "2d ago", or a date. */
function timeAgo(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const mins = Math.floor((Date.now() - then) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}
