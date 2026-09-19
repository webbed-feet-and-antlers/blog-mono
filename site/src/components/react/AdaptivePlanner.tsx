import { useState } from 'react';

/**
 * The adaptive study-plan engine in three passes: a grounding packet (what's
 * true right now), one LLM planner pass (daily buckets from the packet), and
 * deterministic validation — which strips the hallucinated document ID the
 * planner invented. Regeneration is throttled to one update per module per
 * day; the day button fast-forwards past the cooldown.
 */

interface Input {
  id: string;
  label: string;
  bucket: string;
}

const INPUTS: Input[] = [
  { id: 'docs', label: 'Module documents', bucket: 'Read: lecture 7 notes (12 pp.)' },
  { id: 'due', label: 'FSRS due concepts', bucket: 'Review 12 due flashcards' },
  { id: 'prereq', label: 'Prerequisite chains', bucket: 'Practice: enzyme prerequisite chain' },
  { id: 'insights', label: 'Learner insights', bucket: 'Heavy block at peak hour (18:00)' },
  { id: 'exam', label: 'Exam countdown', bucket: 'Timed mock: past paper Q1–Q6 · 23 days left' },
];

const HALLUCINATION = 'Quiz: summary of doc_9f3e1';
const STRIP_NOTE = 'stripped: doc_9f3e1 — not in the module registry';

const PRIMARY_BTN =
  'w-full rounded-md bg-brand-600 px-3 py-1 text-sm font-medium text-white transition-colors hover:bg-brand-700 dark:bg-brand-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 focus-visible:ring-offset-zinc-950 dark:focus-visible:ring-offset-white disabled:cursor-not-allowed disabled:opacity-50';
const SECONDARY_BTN =
  'w-full rounded-md border border-zinc-700 px-3 py-1 font-mono text-xs text-zinc-300 transition-colors hover:bg-zinc-800 dark:border-zinc-300 dark:text-zinc-600 dark:hover:bg-zinc-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 focus-visible:ring-offset-zinc-950 dark:focus-visible:ring-offset-white';
const COL_TITLE = 'mb-2 font-mono text-[10px] uppercase tracking-wide text-zinc-500 dark:text-zinc-400';
const ITEM = 'font-mono text-[11px] text-zinc-300 dark:text-zinc-600';

const initialOn: Record<string, boolean> = Object.fromEntries(INPUTS.map((x) => [x.id, true]));

export default function AdaptivePlanner() {
  const [on, setOn] = useState<Record<string, boolean>>(initialOn);
  const [plan, setPlan] = useState<string[]>(INPUTS.map((x) => x.bucket));
  const [day, setDay] = useState(1);
  const [cooldown, setCooldown] = useState(false);

  const regenerate = () => {
    setPlan(INPUTS.filter((x) => on[x.id]).map((x) => x.bucket));
    setCooldown(true);
  };

  const nextDay = () => {
    setDay((d) => d + 1);
    setCooldown(false);
  };

  const toggle = (id: string) => setOn((o) => ({ ...o, [id]: !o[id] }));

  return (
    <div className="not-prose my-6 rounded-xl border border-zinc-800 bg-zinc-900 p-4 dark:border-zinc-200 dark:bg-zinc-50">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-zinc-100 dark:text-zinc-900">Adaptive study-plan engine</p>
          <p className="font-mono text-xs text-zinc-400 dark:text-zinc-500">
            day {day} · cooldown {cooldown ? 'active' : 'clear'} · {plan.length} item(s)
          </p>
        </div>
      </div>

      <div className="grid gap-3 md:grid-cols-3">
        <div className="rounded-lg bg-zinc-950 p-3 dark:bg-white">
          <p className={COL_TITLE}>grounding packet</p>
          <div className="flex flex-col gap-1.5">
            {INPUTS.map((x) => (
              <label key={x.id} className="flex items-center gap-2 font-mono text-[11px] text-zinc-300 dark:text-zinc-600">
                <input type="checkbox" checked={on[x.id]} onChange={() => toggle(x.id)} className="accent-brand-600" />
                {x.label}
              </label>
            ))}
          </div>
        </div>

        <div className="rounded-lg bg-zinc-950 p-3 dark:bg-white">
          <p className={COL_TITLE}>LLM planner pass</p>
          <div className="mb-2 flex flex-col gap-1">
            <button onClick={regenerate} disabled={cooldown} className={PRIMARY_BTN}>
              Regenerate plan
            </button>
            <button onClick={nextDay} className={SECONDARY_BTN}>
              + 1 day
            </button>
          </div>
          {cooldown && (
            <p className="mb-2 font-mono text-[10px] text-amber-300 dark:text-amber-800">
              cooldown — one regeneration per module per day
            </p>
          )}
          <ul className="flex flex-col gap-1">
            {plan.length === 0 && <li className={ITEM}>nothing grounded — no plan</li>}
            {plan.map((b) => (
              <li key={b} className={ITEM}>
                {b}
              </li>
            ))}
            {plan.length > 0 && (
              <li className="font-mono text-[11px] text-amber-300 dark:text-amber-800">{HALLUCINATION}</li>
            )}
          </ul>
        </div>

        <div className="rounded-lg bg-zinc-950 p-3 dark:bg-white">
          <p className={COL_TITLE}>deterministic validation</p>
          <ul className="flex flex-col gap-1">
            {plan.length === 0 && <li className={ITEM}>no valid items</li>}
            {plan.map((b) => (
              <li key={b} className="flex items-center gap-2">
                <span aria-hidden="true" className="font-mono text-[11px] text-emerald-400 dark:text-emerald-600">
                  ✓
                </span>
                <span className={ITEM}>{b}</span>
              </li>
            ))}
            {plan.length > 0 && (
              <li className="mt-1 font-mono text-[10px] text-amber-300 dark:text-amber-800">✗ {STRIP_NOTE}</li>
            )}
          </ul>
        </div>
      </div>

      <p className="mt-1 text-center text-[11px] text-zinc-400 dark:text-zinc-500">
        Toggle the grounding inputs and regenerate — the planner always invents one document ID, and validation drops
        it. One regeneration per module per day; advance the day to go again.
      </p>
    </div>
  );
}
