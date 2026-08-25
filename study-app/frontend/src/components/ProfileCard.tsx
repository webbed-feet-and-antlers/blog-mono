import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { GraduationCap, Brain } from "lucide-react";
import * as api from "../api/client";
import { Badge } from "@/components/ui/badge";
import { UnderstandingModal } from "./UnderstandingModal";

/** Solid pill per learner level, matching the hand-rolled badge colors. */
const LEVEL_VARIANTS: Record<string, string> = {
  beginner: "bg-warn text-white",
  intermediate: "bg-primary text-white",
  advanced: "bg-ok text-white",
  unknown: "bg-muted-foreground/50 text-white",
};

/**
 * Shows the agent's current understanding of the learner — level, stats,
 * and preferences. Makes the personalization legible: the user can see
 * that the agent is learning about them. Click through for the full
 * "How the agent sees you" view (reflection, patterns, engagement).
 */
export function ProfileCard() {
  const [showUnderstanding, setShowUnderstanding] = useState(false);
  const profile = useQuery({
    queryKey: ["learner-profile"],
    queryFn: api.getLearnerProfile,
    refetchInterval: 10000, // refresh periodically so it stays current
  });

  const p = profile.data;
  if (!p) return null;

  const level = p.learner_level;
  const stats = p.stats;
  const isUnknown = level === "unknown";

  return (
    <>
      <button
        type="button"
        className={`profile-card profile-card-clickable ${isUnknown ? "unknown" : ""}`}
        onClick={() => setShowUnderstanding(true)}
        title="How the agent sees you"
      >
        <div className="profile-level-row">
          <Badge
            className={`px-2 py-0.5 text-[0.72rem] font-bold tracking-wide uppercase ${LEVEL_VARIANTS[level] ?? LEVEL_VARIANTS.unknown}`}
          >
            {level === "unknown" ? "New" : level}
          </Badge>
          {/* The badge already states the level; only label the unknown state. */}
          {isUnknown && (
            <span className="profile-level-label">Learner</span>
          )}
          <Brain size={14} className="profile-card-brain" />
        </div>
        <div className="profile-stats">
          {stats.total_quizzes > 0 ? (
            <>
              <div className="stat-line">
                <GraduationCap size={12} />
                {stats.total_quizzes} quiz{stats.total_quizzes !== 1 ? "zes" : ""}
                {stats.avg_score !== null &&
                  ` · avg ${Math.round(stats.avg_score * 100)}%`}
                {stats.total_flashcard_reviews > 0 &&
                  ` · ${stats.total_flashcard_reviews} card reviews`}
              </div>
              <div className="stat-line">
                prefers {p.preferred_difficulty} difficulty
                {p.preferred_formats.quiz_length &&
                  ` · ${p.preferred_formats.quiz_length}Q`}
                {p.study_goal !== "unknown" &&
                  ` · ${p.study_goal.replace("_", " ")}`}
              </div>
            </>
          ) : (
            <div className="stat-line">
              Take quizzes to build your profile
            </div>
          )}
        </div>
      </button>
      {showUnderstanding && (
        <UnderstandingModal onClose={() => setShowUnderstanding(false)} />
      )}
    </>
  );
}
