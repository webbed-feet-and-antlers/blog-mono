import { useQuery } from "@tanstack/react-query";
import { Zap, Lock, ArrowUp } from "lucide-react";
import * as api from "../api/client";
import type { ConceptWithGraph } from "../types";

/**
 * Shows the agent's concept knowledge graph + mastery model as a structured
 * list. Each concept shows mastery level, due status, prerequisites (with
 * their mastery), and module context. Sorted by priority: due → weakest →
 * prerequisite-blocked.
 */
export function ConceptListView() {
  const concepts = useQuery({
    queryKey: ["concepts"],
    queryFn: api.listConcepts,
  });

  if (concepts.isLoading) {
    return <div className="loading">Loading concepts…</div>;
  }

  if (!concepts.data || concepts.data.length === 0) {
    return (
      <div className="empty">
        No concepts yet. Upload a document and the agent will build the
        concept graph automatically.
      </div>
    );
  }

  return (
    <div className="concept-list">
      <div className="concept-summary">
        {concepts.data.length} concepts tracked ·{" "}
        {concepts.data.filter((c) => c.due).length} due for review ·{" "}
        {concepts.data.filter((c) => c.mastery_pct !== null && c.mastery_pct >= 0.7).length} mastered
      </div>

      {concepts.data.map((c) => (
        <ConceptRow key={c.concept} concept={c} />
      ))}
    </div>
  );
}

export function ConceptRow({
  concept: c,
  onSelect,
}: {
  concept: ConceptWithGraph;
  onSelect?: (concept: string) => void;
}) {
  const pct = c.mastery_pct;
  const dotClass =
    pct === null
      ? "mastery-dot new"
      : pct < 0.4
        ? "mastery-dot very-weak"
        : pct < 0.7
          ? "mastery-dot weak"
          : "mastery-dot strong";

  const label =
    pct === null
      ? "untested"
      : pct < 0.4
        ? "very weak"
        : pct < 0.7
          ? "weak"
          : "mastered";

  return (
    <div
      className={`concept-row ${onSelect ? "clickable" : ""}`}
      onClick={onSelect ? () => onSelect(c.concept) : undefined}
    >
      <div className="concept-main">
        <span className={dotClass} />
        <span className="concept-name">{c.concept}</span>
        <span className="concept-pct">
          {pct !== null ? `${Math.round(pct * 100)}%` : "new"}
        </span>
        <span className="concept-label">{label}</span>
        {c.due && (
          <span className="concept-due-badge">
            <Zap size={11} />
            {c.due_in_days !== null && c.due_in_days < -1
              ? `${Math.abs(Math.round(c.due_in_days))}d overdue`
              : "due"}
          </span>
        )}
        {c.prerequisite_blocked && (
          <span className="concept-blocked-badge">
            <Lock size={11} />
            prereq needed
          </span>
        )}
      </div>

      {c.prerequisites.length > 0 && (
        <div className="concept-prereqs">
          {c.prerequisite_mastery.map((pm) => {
            const pmClass =
              pm.mastery_pct === null
                ? "prereq-mastery new"
                : pm.mastery_pct < 0.4
                  ? "prereq-mastery very-weak"
                  : pm.mastery_pct < 0.7
                    ? "prereq-mastery weak"
                    : "prereq-mastery strong";
            return (
              <div key={pm.concept} className="prereq-line">
                <ArrowUp size={11} className="prereq-arrow" />
                <span className={pmClass}>
                  {pm.concept}
                  {pm.mastery_pct !== null
                    ? ` (${Math.round(pm.mastery_pct * 100)}%)`
                    : " (untested)"}
                </span>
              </div>
            );
          })}
        </div>
      )}

      {c.modules.length > 0 && (
        <div className="concept-modules">
          {c.modules.map((m) => (
            <span key={m} className="concept-module-tag">{m}</span>
          ))}
        </div>
      )}
    </div>
  );
}
