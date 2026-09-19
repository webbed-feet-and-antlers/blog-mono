import { useEffect, useState } from 'react';

/**
 * The five-layer system architecture. Click a layer to expand what it's
 * responsible for; "Trace an event" walks a QuizAttempted through the stack
 * — UI, event bus, agent backbone, decision layer — the way it actually
 * travels at runtime.
 */

interface Layer {
  id: string;
  name: string;
  chips: string[];
  detail: string;
}

const LAYERS: Layer[] = [
  {
    id: 'ui',
    name: 'Application UI',
    chips: ['Modules & Drive', 'Study Sessions', 'Concepts', 'Adaptive Plans'],
    detail:
      'The only layer the student touches. Ordinary UI — decks, calendars, progress bars. User actions and telemetry batches flow down the stack.',
  },
  {
    id: 'ingestion',
    name: 'Ingestion & Boundary',
    chips: ['LibreOffice → PDF', 'Qwen3-ASR transcription', 'Filename normalization'],
    detail:
      'Everything foreign becomes standardised: .docx, .pptx and raw audio go in; viewable PDFs and plain markdown for the agent come out.',
  },
  {
    id: 'bus',
    name: 'Event Bus & Reaction Ledger',
    chips: ['Sync DB commits', 'Async dispatch', 'agent_events log'],
    detail:
      'Primary writes commit synchronously; side effects publish as events that run in isolated handlers. Every dispatch and failure lands in an append-only ledger.',
  },
  {
    id: 'backbone',
    name: 'Core Agent Backbone',
    chips: ['6-node LangGraph pipeline', 'agent_memory store', 'FSRS + knowledge graph'],
    detail:
      'Every generation task — flashcards, quizzes, notes, plans — runs through one pipeline that reads and writes the shared memory store.',
  },
  {
    id: 'decision',
    name: 'Decision & Evaluation',
    chips: ['LinUCB recommender', 'Adaptive planner', 'Evaluation harness'],
    detail:
      'Scores what to study next, paces plans toward exam dates, and measures whether any of it actually works.',
  },
];

const TRACE = [
  { layer: 0, note: 'QuizAttempted — answer + latency captured' },
  { layer: 2, note: 'published on the bus; the DB commit is already done' },
  { layer: 3, note: 'handler: update_concept_mastery · FSRS reschedule · memory write' },
  { layer: 4, note: 'recommender re-scores; plan marked stale (cooldown applies)' },
];

const PRIMARY_BTN =
  'rounded-md bg-brand-600 px-3 py-1 text-sm font-medium text-white transition-colors hover:bg-brand-700 dark:bg-brand-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 focus-visible:ring-offset-zinc-950 dark:focus-visible:ring-offset-white';

export default function SystemArchitecture() {
  const [selected, setSelected] = useState<number | null>(0);
  const [traceStep, setTraceStep] = useState(-1);
  const tracing = traceStep >= 0;

  useEffect(() => {
    if (!tracing) return;
    const t = setInterval(() => {
      setTraceStep((s) => (s >= TRACE.length - 1 ? -1 : s + 1));
    }, 900);
    return () => clearInterval(t);
  }, [tracing]);

  const trace = traceStep >= 0 ? TRACE[traceStep] : null;

  return (
    <div className="not-prose my-6 rounded-xl border border-zinc-800 bg-zinc-900 p-4 dark:border-zinc-200 dark:bg-zinc-50">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-zinc-100 dark:text-zinc-900">System architecture</p>
          <p className="font-mono text-xs text-zinc-400 dark:text-zinc-500">
            5 layers · centred on one memory store and one event bus
          </p>
        </div>
        <button onClick={() => setTraceStep(0)} disabled={tracing} className={`${PRIMARY_BTN} disabled:cursor-not-allowed disabled:opacity-50`}>
          {tracing ? 'Tracing…' : 'Trace an event'}
        </button>
      </div>

      <div className="flex flex-col">
        {LAYERS.map((l, i) => {
          const traced = trace?.layer === i;
          const isOpen = selected === i;
          return (
            <div key={l.id}>
              <div
                className={`overflow-hidden rounded-lg border bg-zinc-950 dark:bg-white ${
                  traced
                    ? 'border-brand-500 dark:border-brand-400'
                    : isOpen
                      ? 'border-brand-500 dark:border-brand-400'
                      : 'border-zinc-800 dark:border-zinc-200'
                }`}
              >
                <button
                  onClick={() => setSelected(isOpen ? null : i)}
                  aria-expanded={isOpen}
                  className="flex w-full flex-wrap items-center justify-between gap-2 px-3 py-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-inset"
                >
                  <span className="font-mono text-xs font-semibold text-zinc-100 dark:text-zinc-900">{l.name}</span>
                  <span className="flex flex-wrap gap-1">
                    {l.chips.map((c) => (
                      <span
                        key={c}
                        className="rounded-sm bg-zinc-800 px-1.5 py-0.5 font-mono text-[9px] text-zinc-300 dark:bg-zinc-100 dark:text-zinc-600"
                      >
                        {c}
                      </span>
                    ))}
                  </span>
                </button>
                {isOpen && (
                  <p className="border-t border-zinc-800 px-3 py-2 text-xs text-zinc-400 dark:border-zinc-200 dark:text-zinc-500">
                    {l.detail}
                  </p>
                )}
              </div>
              {i < LAYERS.length - 1 && (
                <div aria-hidden="true" className="flex justify-center py-0.5 font-mono text-xs text-zinc-600 dark:text-zinc-400">
                  ↓
                </div>
              )}
            </div>
          );
        })}
      </div>

      <p
        className={`mt-3 font-mono text-xs ${
          trace ? 'text-brand-300 dark:text-brand-600' : 'text-zinc-400 dark:text-zinc-500'
        }`}
      >
        {trace ? `QuizAttempted → ${trace.note}` : 'Click a layer to expand it, or trace a quiz submission through the stack.'}
      </p>
    </div>
  );
}
