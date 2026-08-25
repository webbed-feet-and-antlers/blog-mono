import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Zap } from "lucide-react";
import * as api from "../api/client";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ConceptRow } from "./ConceptListView";
import { ConceptDetailModal } from "./ConceptDetailModal";

interface Props {
  onStudySession: () => void;
}

type FilterKey = "all" | "due" | "weak" | "mastered" | "untested";

/**
 * Global concepts overview: every concept across every document, with mastery,
 * FSRS due status, and recall probability. Filterable. Includes a one-click
 * study CTA for due concepts. Clicking a concept opens a detail modal showing
 * its documents, quiz questions, and flashcards.
 */
export function ConceptsPage({ onStudySession }: Props) {
  const [filter, setFilter] = useState<FilterKey>("all");
  const [moduleFilter, setModuleFilter] = useState<string | null>(null);
  const [selectedConcept, setSelectedConcept] = useState<string | null>(null);

  const concepts = useQuery({
    queryKey: ["concepts"],
    queryFn: api.listConcepts,
  });

  if (concepts.isLoading) {
    return (
      <div className="loading concepts-loading">
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
        <Button className="concepts-study-cta" onClick={onStudySession}>
          <Zap size={16} />
          Study {counts.due} due {counts.due === 1 ? "concept" : "concepts"}
        </Button>
      )}

      <div className="concepts-filter-bar">
        <Tabs value={filter} onValueChange={(v) => setFilter(v as FilterKey)}>
          <TabsList>
            {filters.map((f) => (
              <TabsTrigger key={f.key} value={f.key} className="gap-1.5">
                {f.label}
                <Badge
                  variant="secondary"
                  className="px-1.5 py-0 text-[0.68rem] tabular-nums"
                >
                  {f.count}
                </Badge>
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>

        {moduleNames.length > 0 && (
          <Select
            value={moduleFilter ?? "all"}
            onValueChange={(v) => setModuleFilter(v === "all" ? null : v)}
          >
            <SelectTrigger className="concepts-module-select w-48">
              <SelectValue placeholder="All modules" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All modules</SelectItem>
              {moduleNames.map((m) => (
                <SelectItem key={m} value={m}>
                  {m}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </div>

      <div className="concept-list">
        {filtered.length === 0 ? (
          <div className="empty concepts-empty-filter">
            No {filter} concepts.
          </div>
        ) : (
          filtered.map((c) => (
            <ConceptRow
              key={c.concept}
              concept={c}
              onSelect={setSelectedConcept}
            />
          ))
        )}
      </div>

      {/* Concept detail modal: documents + questions + cards referencing it */}
      {selectedConcept && (
        <ConceptDetailModal
          concept={selectedConcept}
          onClose={() => setSelectedConcept(null)}
        />
      )}
    </div>
  );
}
