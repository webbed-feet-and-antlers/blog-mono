import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Zap,
  FileText,
  CircleHelp,
  Layers,
  Sparkles,
  ArrowRight,
  BookOpen,
  Upload,
  X,
} from "lucide-react";
import * as api from "../api/client";
import type { Recommendation } from "../api/client";

interface Props {
  onNavigate: (docId: string, tab?: string) => void;
  onGenerate: (docId: string, taskType: string) => void;
  onStudySession: () => void;
}

const ACTION_ICONS: Record<string, typeof Zap> = {
  review_flashcards: Zap,
  generate_flashcards: Layers,
  generate_quiz: CircleHelp,
  generate_notes: FileText,
  onboarding: Upload,
  view_document: BookOpen,
};

export function RecommendationPanel({ onNavigate, onGenerate, onStudySession }: Props) {
  const queryClient = useQueryClient();
  const rec = useQuery({
    queryKey: ["recommend"],
    queryFn: api.getRecommendation,
    refetchInterval: 15000,
  });

  if (rec.isLoading || !rec.data) {
    return (
      <div className="empty-hero">
        <div className="empty-icon">
          <Sparkles size={34} strokeWidth={1.8} className="spinner" />
        </div>
        <h2>Thinking…</h2>
        <p>The agent is figuring out what you should study next.</p>
      </div>
    );
  }

  const { primary, alternatives, context, impression_id } = rec.data;

  function handleAction(r: Recommendation) {
    // Fire telemetry: user clicked this recommendation.
    if (impression_id && r.strategy_name) {
      api.submitRecommendationFeedback(
        impression_id,
        r.strategy_name,
        "clicked",
      );
    }
    if (!r.document_id && r.action !== "review_flashcards") return;
    // Study session: compose a review+new mix instead of navigating to a doc tab.
    if (r.action === "review_flashcards") {
      onStudySession();
      return;
    }
    if (r.ready) {
      onNavigate(r.document_id!, r.tab ?? undefined);
    } else if (r.action.startsWith("generate_")) {
      const taskType = r.action.replace("generate_", "");
      onGenerate(r.document_id!, taskType);
    } else {
      onNavigate(r.document_id!, r.tab ?? undefined);
    }
  }

  function handleDismiss(r: Recommendation) {
    // Fire telemetry: user dismissed this recommendation.
    if (impression_id && r.strategy_name) {
      api.submitRecommendationFeedback(
        impression_id,
        r.strategy_name,
        "dismissed",
      );
    }
    // Refresh recommendations so the next-best-action appears.
    queryClient.invalidateQueries({ queryKey: ["recommend"] });
  }

  if (!primary) {
    return (
      <div className="empty-hero">
        <div className="empty-icon">
          <BookOpen size={34} strokeWidth={1.8} />
        </div>
        <h2>All caught up</h2>
        <p>No recommendations right now. Upload a document to get started.</p>
      </div>
    );
  }

  const PrimaryIcon = ACTION_ICONS[primary.action] || Sparkles;

  return (
    <div className="recommend-panel">
      {/* Context line */}
      <div className="rec-context">
        {context.welcome_back && (
          <span className="rec-context-item">{context.welcome_back}</span>
        )}
        {context.learner_level !== "unknown" && (
          <>
            {context.welcome_back && <span className="rec-context-sep">·</span>}
            <span className="rec-context-item">
              {context.learner_level} learner
              {context.total_concepts > 0 &&
                ` · ${context.mastered_count}/${context.total_concepts} mastered`}
            </span>
          </>
        )}
        {context.due_count > 0 && (
          <>
            <span className="rec-context-sep">·</span>
            <span className="rec-context-item rec-due">
              <Zap size={12} />
              {context.due_count} due
            </span>
          </>
        )}
      </div>

      {/* Primary recommendation */}
      <div className="rec-primary">
        <div className="rec-primary-icon">
          <PrimaryIcon size={24} />
        </div>
        <div className="rec-primary-body">
          <h2>{primary.title}</h2>
          <p>{primary.rationale}</p>
        </div>
        <div className="rec-primary-actions">
          {primary.dismissible && (
            <button
              className="ghost icon-btn rec-dismiss-btn"
              title="Not now"
              onClick={() => handleDismiss(primary)}
            >
              <X size={16} />
            </button>
          )}
          <button
            className="primary rec-action-btn"
            onClick={() => handleAction(primary)}
          >
            {primary.ready ? (
              <>
                Start now
                <ArrowRight size={16} />
              </>
            ) : (
              <>
                <Sparkles size={16} />
                Generate
              </>
            )}
          </button>
        </div>
      </div>

      {/* Alternatives */}
      {alternatives.length > 0 && (
        <div className="rec-alternatives">
          <div className="rec-alt-label">Or try</div>
          {alternatives.map((alt, i) => {
            const AltIcon = ACTION_ICONS[alt.action] || FileText;
            return (
              <button
                key={i}
                className="rec-alt-card"
                onClick={() => handleAction(alt)}
              >
                <AltIcon size={18} className="rec-alt-icon" />
                <div className="rec-alt-body">
                  <span className="rec-alt-title">{alt.title}</span>
                  <span className="rec-alt-rationale">{alt.rationale}</span>
                </div>
                <ArrowRight size={15} className="rec-alt-arrow" />
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
