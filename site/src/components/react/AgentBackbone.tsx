import { useEffect, useState } from 'react';

/**
 * The unified agent backbone: every generation task — flashcards, quizzes,
 * notes — runs through the same six-node LangGraph pipeline, reading and
 * writing one generic memory store. Pick a task type (the `task_type`
 * parameter picks the Generate tool) and run it: Retrieve Memory highlights
 * the store rows being read, Finalize the row being written back.
 */

type TaskId = 'flashcards' | 'quiz' | 'notes';

interface Task {
  id: TaskId;
  label: string;
  generate: string;
  writes: string;
}

const TASKS: Task[] = [
  { id: 'flashcards', label: 'flashcards', generate: 'Generate flashcards — two or three phrasings per concept, fed into FSRS.', writes: 'FSRS Schedules' },
  { id: 'quiz', label: 'quiz', generate: 'Generate a four-option multiple-choice quiz grounded in the document.', writes: 'Concept Mastery Tallies' },
  { id: 'notes', label: 'notes summary', generate: 'Generate a structured notes summary of the document.', writes: 'Document Analysis' },
];

const NODES = [
  { label: 'Analyze', caption: 'Read the document; extract concepts and structure.' },
  { label: 'Plan', caption: 'Decide the output parameters for this task.' },
  { label: 'Retrieve Memory', caption: 'Pull what the store knows about this learner.' },
  { label: 'Generate', caption: 'Run the task tool with the retrieved memory in context.' },
  { label: 'Validate', caption: 'Check structure, dedupe, drop malformed items.' },
  { label: 'Finalize', caption: 'Save the result and write the new facts back to memory.' },
];

const STORE_ROWS = [
  'Document Analysis',
  'Concept Mastery Tallies',
  'FSRS Schedules',
  'Learner Behavioral Profile',
  'Adaptive Study Plans',
  'Prerequisite Knowledge Graph',
];
const READS = ['Learner Behavioral Profile', 'Prerequisite Knowledge Graph'];

interface RunState {
  node: number;
  running: boolean;
}

const NODE_ACTIVE =
  'rounded-md border border-brand-600 bg-brand-600 px-2 py-1.5 font-mono text-[10px] font-semibold text-white dark:border-brand-600 dark:bg-brand-600';
const NODE_DONE =
  'rounded-md border border-zinc-600 bg-zinc-800 px-2 py-1.5 font-mono text-[10px] text-zinc-300 dark:border-zinc-400 dark:bg-zinc-100 dark:text-zinc-700';
const NODE_IDLE =
  'rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1.5 font-mono text-[10px] text-zinc-500 dark:border-zinc-300 dark:bg-zinc-50 dark:text-zinc-400';
const PRIMARY_BTN =
  'rounded-md bg-brand-600 px-3 py-1 text-sm font-medium text-white transition-colors hover:bg-brand-700 dark:bg-brand-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 focus-visible:ring-offset-zinc-950 dark:focus-visible:ring-offset-white';

export default function AgentBackbone() {
  const [task, setTask] = useState<TaskId>('flashcards');
  const [run, setRun] = useState<RunState>({ node: 3, running: false });

  useEffect(() => {
    if (!run.running) return;
    const t = setInterval(() => {
      setRun((r) =>
        r.node >= NODES.length - 1 ? { node: r.node, running: false } : { node: r.node + 1, running: true },
      );
    }, 1300);
    return () => clearInterval(t);
  }, [run.running]);

  const activeTask = TASKS.find((t) => t.id === task)!;
  const done = !run.running && run.node === NODES.length - 1;
  const caption = run.node === 3 ? activeTask.generate : NODES[run.node].caption;

  const nodeCls = (i: number) => (i === run.node ? NODE_ACTIVE : i < run.node ? NODE_DONE : NODE_IDLE);

  return (
    <div className="not-prose my-6 rounded-xl border border-zinc-800 bg-zinc-900 p-4 dark:border-zinc-200 dark:bg-zinc-50">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-zinc-100 dark:text-zinc-900">Unified agent backbone</p>
          <p className="font-mono text-xs text-zinc-400 dark:text-zinc-500">
            task_type = {activeTask.label} · step {run.node + 1}/6 · {run.running ? 'running' : done ? 'complete' : 'idle'}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-1">
          {TASKS.map((t) => (
            <button
              key={t.id}
              onClick={() => setTask(t.id)}
              aria-pressed={task === t.id}
              className={`rounded-md px-2.5 py-1 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 focus-visible:ring-offset-zinc-950 dark:focus-visible:ring-offset-white ${
                task === t.id
                  ? 'bg-brand-600 text-white dark:bg-brand-600'
                  : 'border border-zinc-700 text-zinc-300 hover:bg-zinc-800 dark:border-zinc-300 dark:text-zinc-600 dark:hover:bg-zinc-100'
              }`}
            >
              {t.label}
            </button>
          ))}
          <button onClick={() => setRun({ node: 0, running: true })} className={PRIMARY_BTN}>
            {run.running ? 'Restart' : 'Run task'}
          </button>
        </div>
      </div>

      <div className="overflow-hidden rounded-lg bg-zinc-950 p-3 dark:bg-white">
        <ol
          className="flex flex-wrap items-center gap-y-2"
          role="img"
          aria-label={`Agent pipeline: ${NODES.map((n) => n.label).join(', ')}. Currently at ${NODES[run.node].label}.`}
        >
          {NODES.map((n, i) => (
            <li key={n.label} className="flex items-center">
              <span className={nodeCls(i)}>{n.label}</span>
              {i < NODES.length - 1 && (
                <span aria-hidden="true" className="px-1 font-mono text-xs text-zinc-600 dark:text-zinc-400">
                  →
                </span>
              )}
            </li>
          ))}
        </ol>
      </div>

      <p className="mt-3 font-mono text-xs text-zinc-300 dark:text-zinc-600">
        {done ? `Complete — result saved and ${activeTask.writes} updated.` : caption}
      </p>

      <div className="mt-3 rounded-lg bg-zinc-950 p-3 dark:bg-white">
        <p className="mb-2 font-mono text-[10px] uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
          agent_memory — one generic store, every feature reads and writes it
        </p>
        <ul className="grid gap-1 sm:grid-cols-2">
          {STORE_ROWS.map((row) => {
            const readRow = run.node === 2 && READS.includes(row);
            const writeRow = run.node === 5 && row === activeTask.writes;
            return (
              <li
                key={row}
                className={`flex items-center justify-between rounded-md px-2 py-1 font-mono text-[10px] ${
                  readRow
                    ? 'bg-emerald-950/40 text-emerald-300 dark:bg-emerald-50 dark:text-emerald-700'
                    : writeRow
                      ? 'bg-brand-950/40 text-brand-300 dark:bg-brand-50 dark:text-brand-700'
                      : 'text-zinc-500 dark:text-zinc-400'
                }`}
              >
                <span>{row}</span>
                {readRow && <span className="text-[9px] uppercase text-emerald-300 dark:text-emerald-700">read</span>}
                {writeRow && <span className="text-[9px] uppercase text-brand-300 dark:text-brand-700">write</span>}
              </li>
            );
          })}
        </ul>
      </div>

      <p className="mt-1 text-center text-[11px] text-zinc-400 dark:text-zinc-500">
        Pick a task type and run it — Retrieve Memory reads the store (emerald), Finalize writes back (brand).
      </p>
    </div>
  );
}
