import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  X,
  Clock,
  Brain,
  Gauge,
  RefreshCw,
  Loader2,
  BookOpen,
  Timer,
} from "lucide-react";
import * as api from "../api/client";
import type { LearnerProfile } from "../api/client";

interface Props {
  onClose: () => void;
}

/**
 * "How the agent sees you" — the full learner model in one view: the LLM
 * reflection's summary + traits, deterministic study patterns (when/how the
 * learner studies), engagement (what they read), and slow-recall concepts.
 *
 * Transparency surface: every signal here already feeds generation prompts,
 * so the user sees exactly what the agent uses to personalize.
 */
export function UnderstandingModal({ onClose }: Props) {
  const queryClient = useQueryClient();
  const [reflecting, setReflecting] = useState(false);
  const [reflectMsg, setReflectMsg] = useState<string | null>(null);

  const profile = useProfileFromCache();

  async function handleRefresh() {
    setReflecting(true);
    setReflectMsg(null);
    try {
      const result = await api.reflectOnLearner(true);
      if (result.status === "updated") {
        await queryClient.invalidateQueries({ queryKey: ["learner-profile"] });
        setReflectMsg(null);
      } else {
        setReflectMsg(
          result.status === "skipped"
            ? `Not enough new activity yet (${result.reason ?? "cooldown"})`
            : `Reflection failed: ${result.reason ?? "unknown error"}`,
        );
      }
    } catch (e) {
      setReflectMsg(`Reflection failed: ${(e as Error).message}`);
    } finally {
      setReflecting(false);
    }
  }

  const insights = profile?.insights;
  const patterns = profile?.patterns;
  const engagement = profile?.engagement;
  const slow = profile?.slow_concepts ?? [];

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal-content understanding-modal"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="concept-detail-header">
          <div className="concept-detail-title-row">
            <h3>
              <Brain size={16} style={{ marginRight: 6, verticalAlign: -2 }} />
              How the agent sees you
            </h3>
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
            every signal below already personalizes what the agent generates
          </div>
        </div>

        {/* Reflection */}
        <div className="concept-detail-section">
          <h4>
            <Brain size={14} />
            Understanding
          </h4>
          {insights?.summary ? (
            <>
              <p className="understanding-summary">{insights.summary}</p>
              {insights.traits.length > 0 && (
                <div className="understanding-traits">
                  {insights.traits.map((t) => (
                    <span key={t} className="understanding-trait">
                      {t}
                    </span>
                  ))}
                </div>
              )}
              {insights.habits && (
                <p className="understanding-habits">{insights.habits}</p>
              )}
            </>
          ) : (
            <div className="concept-detail-none">
              The agent is still getting to know you — keep studying and it
              will build an understanding from your behavior.
            </div>
          )}
          <div className="understanding-refresh-row">
            <button
              type="button"
              className="ghost"
              disabled={reflecting}
              onClick={handleRefresh}
            >
              {reflecting ? (
                <Loader2 size={14} className="spinner" />
              ) : (
                <RefreshCw size={14} />
              )}
              Refresh understanding
            </button>
            {reflectMsg && (
              <span className="understanding-refresh-msg">{reflectMsg}</span>
            )}
          </div>
        </div>

        {/* Study patterns */}
        <div className="concept-detail-section">
          <h4>
            <Clock size={14} />
            Study patterns
          </h4>
          {patterns ? (
            <div className="understanding-grid">
              <Stat
                label="Most active"
                value={
                  patterns.best_study_hour !== null
                    ? `${String(patterns.best_study_hour).padStart(2, "0")}:00 UTC`
                    : "—"
                }
              />
              <Stat
                label="Avg quiz time"
                value={
                  patterns.avg_quiz_duration_secs
                    ? `${Math.round(patterns.avg_quiz_duration_secs)}s`
                    : "—"
                }
              />
              <Stat
                label="Sessions completed"
                value={`${patterns.sessions.completed}/${patterns.sessions.completed + patterns.sessions.abandoned}`}
              />
              <Stat
                label="Actions tracked"
                value={engagement ? String(engagement.actions_count) : "0"}
              />
            </div>
          ) : (
            <div className="concept-detail-none">No patterns yet.</div>
          )}
        </div>

        {/* Engagement */}
        {engagement && engagement.top_docs.length > 0 && (
          <div className="concept-detail-section">
            <h4>
              <BookOpen size={14} />
              Where your time goes
            </h4>
            <div className="understanding-docs">
              {engagement.top_docs.map((d) => (
                <div key={d.doc_id} className="understanding-doc">
                  <span className="cdi-main">
                    {d.topic ?? d.doc_id}
                  </span>
                  <span className="cdi-sub">
                    <Timer size={11} style={{ verticalAlign: -1 }} />{" "}
                    {formatDuration(d.dwell_secs)} · {d.views} view
                    {d.views === 1 ? "" : "s"}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Slow recall */}
        {slow.length > 0 && (
          <div className="concept-detail-section">
            <h4>
              <Gauge size={14} />
              Slow recall
            </h4>
            <div className="understanding-docs">
              {slow.map((c) => (
                <div key={c.concept} className="understanding-doc">
                  <span className="cdi-main">{c.concept}</span>
                  <span className="cdi-sub">~{c.avg_secs}s avg answer</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/** Read the profile the ProfileCard query already caches. */
function useProfileFromCache(): LearnerProfile | undefined {
  const queryClient = useQueryClient();
  return queryClient.getQueryData<LearnerProfile>(["learner-profile"]);
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="understanding-stat">
      <div className="understanding-stat-value">{value}</div>
      <div className="understanding-stat-label">{label}</div>
    </div>
  );
}

function formatDuration(secs: number): string {
  if (secs < 60) return `${secs}s`;
  const m = Math.floor(secs / 60);
  if (m < 60) return `${m}m`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}
