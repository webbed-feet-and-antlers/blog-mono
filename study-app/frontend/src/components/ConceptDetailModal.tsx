import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import {
  X,
  Loader2,
  FileText,
  CircleHelp,
  Layers,
  Zap,
  ChevronRight,
} from "lucide-react";
import * as api from "../api/client";
import { track } from "../api/track";

interface Props {
  concept: string;
  onClose: () => void;
}

/**
 * Modal showing everything that references a concept: the documents it was
 * extracted from, quiz questions tagged with it, and flashcards that test it.
 * Every reference is clickable and navigates to its source.
 */
export function ConceptDetailModal({ concept, onClose }: Props) {
  const navigate = useNavigate();
  const refs = useQuery({
    queryKey: ["concept-references", concept],
    queryFn: () => api.getConceptReferences(concept),
  });

  // Opening a concept's detail view is a curiosity signal.
  useEffect(() => {
    track("concept.viewed", { concept });
  }, [concept]);

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal-content concept-detail-modal"
        onClick={(e) => e.stopPropagation()}
      >
        {refs.isLoading && (
          <div className="loading concept-detail-loading">
            <Loader2 size={18} className="spinner" />
            Loading references…
          </div>
        )}

        {refs.isError && (
          <div className="error">
            Failed to load references: {(refs.error as Error).message}
          </div>
        )}

        {refs.data && (
          <>
            <div className="concept-detail-header">
              <div className="concept-detail-title-row">
                <h3>{refs.data.concept}</h3>
                <button
                  type="button"
                  className="ghost icon-btn"
                  onClick={onClose}
                  aria-label="Close"
                >
                  <X size={16} />
                </button>
              </div>
              <div className="concept-detail-stats">
                {refs.data.mastery_pct !== null
                  ? `${Math.round(refs.data.mastery_pct * 100)}% mastery`
                  : "Untested"}
                {" · "}
                {refs.data.seen} review{refs.data.seen === 1 ? "" : "s"}
                {refs.data.retrievability !== null &&
                  ` · ${Math.round(refs.data.retrievability * 100)}% recall now`}
                {refs.data.due && (
                  <span className="concept-due-badge concept-detail-due">
                    <Zap size={11} />
                    due
                  </span>
                )}
              </div>
            </div>

            {/* Documents */}
            <div className="concept-detail-section">
              <h4>
                <FileText size={14} />
                Documents ({refs.data.documents.length})
              </h4>
              {refs.data.documents.length === 0 ? (
                <div className="concept-detail-none">No documents reference this concept.</div>
              ) : (
                refs.data.documents.map((d) => (
                  <button
                    key={d.id}
                    type="button"
                    className="concept-detail-item"
                    onClick={() => {
                      track("concept.reference_clicked", {
                        concept,
                        target: "document",
                      });
                      onClose();
                      navigate({
                        to: "/documents/$docId",
                        params: { docId: d.id },
                      });
                    }}
                  >
                    <FileText size={14} className="cdi-icon" />
                    <span className="cdi-main">{d.filename}</span>
                    {d.topic && <span className="cdi-sub">{d.topic}</span>}
                    <ChevronRight size={13} className="cdi-arrow" />
                  </button>
                ))
              )}
            </div>

            {/* Quiz questions */}
            <div className="concept-detail-section">
              <h4>
                <CircleHelp size={14} />
                Quiz questions ({refs.data.quiz_questions.length})
              </h4>
              {refs.data.quiz_questions.length === 0 ? (
                <div className="concept-detail-none">
                  No quiz questions test this concept yet.
                </div>
              ) : (
                refs.data.quiz_questions.map((q, i) => (
                  <button
                    key={`${q.content_id}-${q.question_id ?? i}`}
                    type="button"
                    className="concept-detail-item"
                    onClick={() => {
                      track("concept.reference_clicked", {
                        concept,
                        target: "quiz",
                      });
                      onClose();
                      navigate({
                        to: "/documents/$docId/$tab",
                        params: { docId: q.document_id, tab: "quiz" },
                      });
                    }}
                  >
                    <CircleHelp size={14} className="cdi-icon" />
                    <span className="cdi-main cdi-prompt">{q.prompt}</span>
                    {q.doc_filename && (
                      <span className="cdi-sub">{q.doc_filename}</span>
                    )}
                    <ChevronRight size={13} className="cdi-arrow" />
                  </button>
                ))
              )}
            </div>

            {/* Flashcards */}
            <div className="concept-detail-section">
              <h4>
                <Layers size={14} />
                Flashcards ({refs.data.flashcards.length})
              </h4>
              {refs.data.flashcards.length === 0 ? (
                <div className="concept-detail-none">
                  No flashcards test this concept yet.
                </div>
              ) : (
                refs.data.flashcards.map((c, i) => (
                  <button
                    key={`${c.content_id}-${c.card_id ?? i}`}
                    type="button"
                    className="concept-detail-item"
                    onClick={() => {
                      track("concept.reference_clicked", {
                        concept,
                        target: "flashcard",
                      });
                      onClose();
                      navigate({
                        to: "/documents/$docId/$tab",
                        params: { docId: c.document_id, tab: "flashcards" },
                      });
                    }}
                  >
                    <Layers size={14} className="cdi-icon" />
                    <span className="cdi-main cdi-prompt">
                      {c.front}
                      <span className="cdi-answer"> → {c.back}</span>
                    </span>
                    {c.doc_filename && (
                      <span className="cdi-sub">{c.doc_filename}</span>
                    )}
                    <ChevronRight size={13} className="cdi-arrow" />
                  </button>
                ))
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
