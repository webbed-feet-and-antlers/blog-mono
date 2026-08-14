import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Zap, Loader2 } from "lucide-react";
import * as api from "../api/client";
import { ConceptRow } from "./ConceptListView";

interface Props {
  onStudySession: () => void;
}

type FilterKey = "all" | "due" | "weak" | "mastered" | "untested";

/**
 * Global concepts overview: every concept across every document, with mastery,
 * FSRS due status, and recall probability. Filterable. Includes a one-click
 * study CTA for due concepts.
 */
export function ConceptsPage({ onStudySession }: Props) {
  const [filter, setFilter] = useState<FilterKey>("all");
  const [moduleFilter, setModuleFilter] = useState<string | null>(null);

  const concepts = useQuery({
    queryKey: ["concepts"],
    queryFn: api.listConcepts,
  });

  if (concepts.isLoading) {
    return (
      <div className="loading concepts-loading">
        <Loader2 size={18} className="spinner" />
        Loading concepts…
      </div>
    );
  }

  if (!concepts.data || concepts.data.length === 0) {
    return (
      <div className="empty concepts-empty">
        No concepts yet. Upload a document and the agent will build the concept
        graph automatically.
      </div>
    );
  }

  const all = concepts.data;
  const counts = {
    all: all.length,
    due: all.filter((c) => c.due).length,
    weak: all.filter((c) => c.mastery_pct !== null && c.mastery_pct < 0.7)
      .length,
    mastered: all.filter((c) => c.mastery_pct !== null && c.mastery_pct >= 0.7)
      .length,
    untested: all.filter((c) => c.mastery_pct === null).length,
  };

  // Average recall from retrievability (only tested concepts contribute).
  const tested = all.filter((c) => c.retrievability !== null);
  const avgRecall =
    tested.length > 0
      ? Math.round(
          (tested.reduce((sum, c) => sum + (c.retrievability ?? 0), 0) /
            tested.length) *
            100,
        )
      : null;

  // Build the list of modules from concepts (some concepts have no module).
  const moduleNames = Array.from(
    new Set(all.flatMap((c) => c.modules)),
  ).sort();

  const filtered = all.filter((c) => {
    // Module filter takes precedence.
    if (moduleFilter && !c.modules.includes(moduleFilter)) return false;
    switch (filter) {
      case "due":
        return c.due;
      case "weak":
        return c.mastery_pct !== null && c.mastery_pct < 0.7;
      case "mastered":
        return c.mastery_pct !== null && c.mastery_pct >= 0.7;
      case "untested":
        return c.mastery_pct === null;
      default:
        return true;
    }
  });

  const filters: { key: FilterKey; label: string; count: number }[] = [
    { key: "all", label: "All", count: counts.all },
    { key: "due", label: "Due", count: counts.due },
    { key: "weak", label: "Weak", count: counts.weak },
    { key: "mastered", label: "Mastered", count: counts.mastered },
    { key: "untested", label: "Untested", count: counts.untested },
  ];

  return (
    <div className="concepts-page">
      <div className="concepts-header">
        <h1>Concepts</h1>
        <div className="concepts-summary">
          {counts.all} concepts tracked · {counts.due} due · {counts.mastered}{" "}
          mastered
          {avgRecall !== null && ` · ${avgRecall}% avg recall`}
        </div>
      </div>

      {counts.due > 0 && (
        <button
          type="button"
          className="primary concepts-study-cta"
          onClick={onStudySession}
        >
          <Zap size={16} />
          Study {counts.due} due {counts.due === 1 ? "concept" : "concepts"}
        </button>
      )}

      <div className="concepts-filter-bar">
        {filters.map((f) => (
          <button
            key={f.key}
            type="button"
            className={`concepts-filter-btn ${filter === f.key ? "active" : ""}`}
            onClick={() => setFilter(f.key)}
          >
            {f.label}
            <span className="count">{f.count}</span>
          </button>
        ))}

        {moduleNames.length > 0 && (
          <select
            className="concepts-module-select"
            value={moduleFilter ?? ""}
            onChange={(e) => setModuleFilter(e.target.value || null)}
          >
            <option value="">All modules</option>
            {moduleNames.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        )}
      </div>

      <div className="concept-list">
        {filtered.length === 0 ? (
          <div className="empty concepts-empty-filter">
            No {filter} concepts.
          </div>
        ) : (
          filtered.map((c) => <ConceptRow key={c.concept} concept={c} />)
        )}
      </div>
    </div>
  );
}
