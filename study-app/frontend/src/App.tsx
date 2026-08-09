import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as api from "./api/client";
import { ApiError } from "./api/client";
import type { ContentItem, TaskType } from "./types";
import { Sidebar } from "./components/Sidebar";
import { NotesView } from "./components/NotesView";
import { QuizView } from "./components/QuizView";
import { FlashcardView } from "./components/FlashcardView";

const TABS: { id: TaskType; label: string }[] = [
  { id: "notes", label: "📝 Notes" },
  { id: "quiz", label: "❓ Quiz" },
  { id: "flashcards", label: "🎴 Flashcards" },
];

export default function App() {
  const queryClient = useQueryClient();
  const [selectedDocId, setSelectedDocId] = useState<string | null>(null);
  const [tab, setTab] = useState<TaskType>("notes");
  const [hint, setHint] = useState("");

  const doc = useQuery({
    queryKey: ["document", selectedDocId],
    queryFn: () => api.getDocument(selectedDocId!),
    enabled: !!selectedDocId,
  });

  const content = useQuery({
    queryKey: ["content", selectedDocId, tab],
    queryFn: () => api.listContent(selectedDocId!, tab),
    enabled: !!selectedDocId,
  });

  const generate = useMutation({
    mutationFn: () =>
      api.generate({
        document_id: selectedDocId!,
        task_type: tab,
        instructions: hint.trim() || undefined,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["content", selectedDocId, tab] });
      queryClient.invalidateQueries({ queryKey: ["memory"] });
    },
  });

  const removeContent = useMutation({
    mutationFn: (id: string) => api.deleteContent(id),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["content", selectedDocId, tab] }),
  });

  const items: ContentItem[] = content.data ?? [];
  const latest = items[0]; // list is ordered newest-first
  const generating = generate.isPending;

  return (
    <div className="app">
      <Sidebar selectedId={selectedDocId} onSelect={setSelectedDocId} />

      <main className="main">
        {!selectedDocId && (
          <div className="empty">
            <h2>No document selected</h2>
            <p>Upload or pick a document from the sidebar to begin.</p>
          </div>
        )}

        {selectedDocId && (
          <>
            <div className="section-head">
              <h2>{doc.data?.filename ?? "Loading…"}</h2>
            </div>

            <div className="tabs">
              {TABS.map((t) => (
                <button
                  key={t.id}
                  className={`tab ${tab === t.id ? "active" : ""}`}
                  onClick={() => setTab(t.id)}
                >
                  {t.label}
                </button>
              ))}
            </div>

            {/* Generate controls */}
            <div style={{ marginBottom: 24 }}>
              <input
                className="hint-input"
                placeholder={
                  tab === "quiz"
                    ? "Optional hint, e.g. 'focus on chapter 3, 10 questions'"
                    : tab === "notes"
                      ? "Optional hint, e.g. 'concise bullet points'"
                      : "Optional hint, e.g. 'definition-style cards'"
                }
                value={hint}
                onChange={(e) => setHint(e.target.value)}
                disabled={generating}
              />
              <button
                className="primary"
                disabled={generating}
                onClick={() => generate.mutate()}
              >
                {generating
                  ? `🤖 Agent is generating ${tab}…`
                  : `Generate ${tab} via agent`}
              </button>{" "}
              {latest && !generating && (
                <button
                  className="small danger"
                  onClick={() => removeContent.mutate(latest.id)}
                >
                  Delete current
                </button>
              )}
            </div>

            {generate.isError && (
              <div className="error">
                Generation failed: {(generate.error as Error).message}
                {generate.error instanceof ApiError &&
                  generate.error.status === 502 && (
                    <span>
                      {" "}
                      — check that OPENROUTER_API_KEY is set in the backend .env
                    </span>
                  )}
              </div>
            )}

            {generating && (
              <div className="loading">
                The agent is reading the document, planning, and generating. This
                takes a few seconds…
              </div>
            )}

            {!generating && latest && (
              <ContentRender item={latest} />
            )}

            {!generating && !latest && !content.isLoading && (
              <div className="empty">
                No {tab} yet. Click <strong>Generate</strong> to have the agent
                create some.
              </div>
            )}
          </>
        )}
      </main>
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
  return <FlashcardView content={item.content as any} />;
}
