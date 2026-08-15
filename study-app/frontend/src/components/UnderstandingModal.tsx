import { useEffect, useState } from "react";
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
  Sparkles,
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

  // Escape closes — while reflecting, let it finish instead of unmounting.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape" && !reflecting) onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [reflecting, onClose]);

  const profile = useProfileFromCache();

  async function handleRefresh() {
    setReflecting(true);
    setReflectMsg(null);
    try {
      const result = await api.reflectOnLearner(true);
      if (result.status === "updated") {
        await queryClient.invalidateQueries({ queryKey: ["learner-profile"] });
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
        {/* Fixed header — the close button stays reachable while the body scrolls. */}
        <div className="understanding-header">
          <div className="understanding-header-icon">
            <Brain size={18} />
          </div>
          <div className="understanding-header-text">
            <h3>How the agent sees you</h3>
            <p>every signal below personalizes what the agent generates</p>
          </div>
          <button
            type="button"
            className="ghost icon-btn"
            onClick={onClose}
            aria-label="Close"
          >
            <X size={16} />
          </button>
        </div>

        <div className="understanding-body">
          {/* Reflection */}
          <div className="concept-detail-section">
            <h4>
              <Sparkles size={14} />
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
                <div className="understanding-meta">
                  <span>
                    based on {insights.activities_seen} tracked actions
                    {insights.updated_at &&
                      ` · updated ${timeAgo(insights.updated_at)}`}
                  </span>
                  <button
                    type="button"
                    className="understanding-refresh"
                    disabled={reflecting}
                    onClick={handleRefresh}
                    title="Re-run the agent's reflection over your recent behavior"
                  >
                    {reflecting ? (
                      <>
                        <Loader2 size={13} className="spinner" />
                        Reflecting…
                      </>
                    ) : (
                      <>
                        <RefreshCw size={13} />
                        Refresh
                      </>
                    )}
                  </button>
                </div>
                {reflectMsg && (
                  <div className="understanding-refresh-msg">{reflectMsg}</div>
                )}
              </>
            ) : (
              <div className="understanding-empty">
                <Brain size={26} strokeWidth={1.5} />
                <p>
                  The agent is still getting to know you. Keep studying — it
                  builds an understanding from how you use the app, not just
                  your scores.
                </p>
                <button
                  type="button"
                  className="understanding-refresh"
                  disabled={reflecting}
                  onClick={handleRefresh}
                >
                  {reflecting ? (
                    <>
                      <Loader2 size={13} className="spinner" />
                      Reflecting…
                    </>
                  ) : (
                    <>
                      <RefreshCw size={13} />
                      Try reflecting now
                    </>
                  )}
                </button>
              </div>
            )}
          </div>

          {/* Study patterns */}
          {patterns && (
            <div className="concept-detail-section">
              <h4>
                <Clock size={14} />
                Study patterns
              </h4>
              <div className="understanding-grid">
                <Stat
                  label="Most active"
                  value={
                    patterns.best_study_hour !== null
                      ? `${String(patterns.best_study_hour).padStart(2, "0")}:00`
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
                  label="Sessions done"
                  value={`${patterns.sessions.completed}/${patterns.sessions.completed + patterns.sessions.abandoned}`}
                />
                <Stat
                  label="Actions tracked"
                  value={engagement ? String(engagement.actions_count) : "0"}
                />
              </div>
              {patterns.hour_histogram?.some((h) => h > 0) && (
                <HourHistogram
                  histogram={patterns.hour_histogram}
                  bestHour={patterns.best_study_hour}
                />
              )}
            </div>
          )}

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
                    <span className="cdi-main">{d.topic ?? d.doc_id}</span>
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
    </div>
  );
}

/** Compact 24-bar hour-of-day histogram — when this learner studies (UTC). */
function HourHistogram({
  histogram,
  bestHour,
}: {
  histogram: number[];
  bestHour: number | null;
}) {
  const max = Math.max(...histogram, 1);
  return (
    <div className="understanding-hist">
      <div className="understanding-hist-bars">
        {histogram.map((count, hour) => (
          <div
            key={hour}
            className={`understanding-hist-bar ${hour === bestHour ? "best" : ""}`}
            style={{ height: `${Math.max(4, (count / max) * 100)}%` }}
            title={`${String(hour).padStart(2, "0")}:00 — ${count} action${count === 1 ? "" : "s"}`}
          />
        ))}
      </div>
      <div className="understanding-hist-labels">
        <span>00</span>
        <span>06</span>
        <span>12</span>
        <span>18</span>
        <span>23</span>
      </div>
      <div className="understanding-hist-caption">when you study (UTC)</div>
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

function timeAgo(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "recently";
  const mins = Math.floor((Date.now() - then) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}
