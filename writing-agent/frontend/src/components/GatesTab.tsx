import { Gauge, Loader2, ShieldCheck, Wand2, Zap } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { runFix, runLint, runScore } from "@/api/hooks";
import type { LintReport, SurprisalReport } from "@/api/types";
import { Badge, type BadgeProps } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { useEditor } from "@/state/editor";
import { cn } from "@/lib/utils";

interface Chip {
  label: string;
  tone: Exclude<BadgeProps["tone"], undefined>;
  detail: string;
}

function buildChips(lint: LintReport | null, score: SurprisalReport | null): Chip[] {
  const chips: Chip[] = [];
  const reasons = lint?.failures.flatMap((f) => f.reasons) ?? [];
  const has = (needle: string) => reasons.some((r) => r.toLowerCase().includes(needle));

  const add = (label: string, failed: boolean, detail: string) =>
    chips.push({
      label,
      tone: failed ? "err" : "ok",
      detail: failed ? detail : "pass",
    });

  add("banned n-grams", (lint?.metrics.banned?.length ?? 0) > 0, `${lint?.metrics.banned?.length ?? 0} hits`);
  add("burstiness", has("monotony"), "sentence-length variance");
  add("transitions", !!lint?.metrics.transitions?.reason, "paragraph openings + em-dash");
  add("deep syntax", has("deep-syntax"), "parentheticals / inversions");
  add("redundancy", (lint?.metrics.redundancy?.length ?? 0) > 0, `${lint?.metrics.redundancy?.length ?? 0} restating pairs`);
  if (score) {
    if (score.skipped) {
      chips.push({ label: "surprisal", tone: "warn", detail: score.warning ?? "skipped" });
    } else {
      add(
        "surprisal",
        score.flagged_blocks.length > 0,
        `${score.flagged_blocks.length} predictable blocks`,
      );
      const convergent = score.blocks.some(
        (b) => b.reason?.includes("convergent") || b.reason?.includes("cross-model"),
      );
      add("convergence", convergent, "blind samples agree");
      if (score.bits_burstiness) {
        add(
          "bits variance",
          score.bits_burstiness.failed,
          `SD ${score.bits_burstiness.bits_sd.toFixed(2)}`,
        );
      }
    }
  }
  return chips;
}

export function GatesTab() {
  const { markdown, proposeChange } = useEditor();
  const [lint, setLint] = useState<LintReport | null>(null);
  const [score, setScore] = useState<SurprisalReport | null>(null);
  const [linting, setLinting] = useState(false);
  const [scoring, setScoring] = useState(false);
  const [fixing, setFixing] = useState<number[] | null>(null);

  const doLint = async () => {
    setLinting(true);
    try {
      const res = await runLint(markdown);
      setLint(res.report);
      if (res.report.passed) toast.success("All deterministic gates pass");
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setLinting(false);
    }
  };

  const doScore = async () => {
    setScoring(true);
    try {
      setScore(await runScore(markdown));
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setScoring(false);
    }
  };

  const doFix = async (indices: number[] | null) => {
    setFixing(indices ?? []);
    try {
      const res = await runFix(markdown, indices);
      if (!res.changed) {
        toast.success("Nothing flagged — all gates pass");
        return;
      }
      proposeChange({
        title: indices ? `Fix block ${indices.join(", ")}` : "Fix all flagged blocks",
        before: markdown,
        after: res.markdown,
        source: "fix",
      });
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setFixing(null);
    }
  };

  const chips = buildChips(lint, score);
  const failures = lint?.failures ?? [];
  const surprisalFails = (score?.blocks ?? []).filter((b) => b.failed);

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 overflow-y-auto p-3">
      <div className="flex flex-wrap gap-2">
        <Button size="sm" onClick={() => void doLint()} disabled={linting || !markdown.trim()}>
          {linting ? <Loader2 size={13} className="animate-spin" /> : <Gauge size={13} />}
          Lint <span className="text-muted">(free)</span>
        </Button>
        <Button size="sm" onClick={() => void doScore()} disabled={scoring || !markdown.trim()}>
          {scoring ? <Loader2 size={13} className="animate-spin" /> : <Zap size={13} />}
          Surprisal <span className="text-muted">(API)</span>
        </Button>
        <Button
          size="sm"
          variant="primary"
          onClick={() => void doFix(null)}
          disabled={fixing !== null || !markdown.trim()}
          title="Rewrite only the blocks failing the gates — pairwise-judged candidates"
        >
          {fixing !== null ? <Loader2 size={13} className="animate-spin" /> : <Wand2 size={13} />}
          AI fix flagged
        </Button>
      </div>

      {chips.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {chips.map((c) => (
            <span key={c.label} title={c.detail}>
              <Badge tone={c.tone}>
                {c.tone === "ok" && <ShieldCheck size={10} />}
                {c.label}
              </Badge>
            </span>
          ))}
        </div>
      )}

      {lint && (
        <pre className="whitespace-pre-wrap rounded-md border border-line bg-panel2 p-2.5 font-mono text-[11px] leading-5 text-muted">
          {lint.scorecard ?? ""}
        </pre>
      )}

      {failures.length > 0 && (
        <div className="flex flex-col gap-2">
          {failures.map((f) => (
            <div key={f.block_index} className="rounded-md border border-warn/40 p-2.5">
              <div className="mb-1.5 flex items-center justify-between">
                <span className="text-[12px] font-semibold text-warn">block {f.block_index}</span>
                <Button size="sm" variant="ghost" onClick={() => void doFix([f.block_index])} disabled={fixing !== null}>
                  <Wand2 size={11} /> Fix this block
                </Button>
              </div>
              <ul className="ml-4 list-disc text-[12px] leading-5 text-muted">
                {f.reasons.map((r, i) => (
                  <li key={i}>{r}</li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}

      {surprisalFails.length > 0 && (
        <div className="flex flex-col gap-2">
          {surprisalFails.map((b) => (
            <div
              key={b.block_index}
              className={cn(
                "rounded-md border p-2.5",
                b.reason?.includes("convergent") || b.reason?.includes("cross-model")
                  ? "border-err/40"
                  : "border-warn/40",
              )}
            >
              <div className="text-[12px] font-semibold text-warn">block {b.block_index}</div>
              <p className="mt-1 text-[12px] leading-5 text-muted">{b.reason}</p>
            </div>
          ))}
        </div>
      )}

      {!lint && !score && (
        <p className="pt-4 text-center text-[12.5px] leading-6 text-muted">
          Run the gates on the current draft. Lint is instant and offline;
          Surprisal asks the blind scorer how predictable each section is.
        </p>
      )}
    </div>
  );
}
