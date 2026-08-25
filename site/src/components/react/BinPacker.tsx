import { useMemo, useState } from 'react';

interface Doc {
  id: number;
  tokens: number;
}

interface PackedBatch {
  docs: Doc[];
  used: number;
  padding: number;
}

const TOKEN_BUDGET = 8192;

/** Sort docs descending by length, then greedily place each into the first batch it fits. */
function firstFitDecreasing(docs: Doc[], budget: number): PackedBatch[] {
  const sorted = [...docs].sort((a, b) => b.tokens - a.tokens);
  const batches: PackedBatch[] = [];
  for (const doc of sorted) {
    let placed = false;
    for (const b of batches) {
      if (b.used + doc.tokens <= budget) {
        b.docs.push(doc);
        b.used += doc.tokens;
        placed = true;
        break;
      }
    }
    if (!placed) {
      batches.push({ docs: [doc], used: doc.tokens, padding: 0 });
    }
  }
  for (const b of batches) b.padding = budget - b.used;
  return batches;
}

const PALETTE = ['#1e5cf5', '#16a34a', '#ea580c', '#9333ea', '#db2777', '#0891b2'];

let nextId = 100;
const PRESET: Doc[] = [
  { id: 1, tokens: 7200 },
  { id: 2, tokens: 1200 },
  { id: 3, tokens: 850 },
  { id: 4, tokens: 3000 },
  { id: 5, tokens: 410 },
  { id: 6, tokens: 6800 },
  { id: 7, tokens: 900 },
];

export default function BinPacker({ budget = TOKEN_BUDGET }: { budget?: number }) {
  const [docs, setDocs] = useState<Doc[]>(PRESET);
  const [nextTokens, setNextTokens] = useState(1500);

  const batches = useMemo(() => firstFitDecreasing(docs, budget), [docs, budget]);
  const totalUsed = batches.reduce((s, b) => s + b.used, 0);
  const totalCap = batches.length * budget;
  const efficiency = totalCap > 0 ? Math.round((totalUsed / totalCap) * 100) : 0;
  const overBudget = docs.filter((d) => d.tokens > budget);

  const addDoc = () => {
    const tokens = Math.max(1, Math.min(budget * 2, Math.round(nextTokens)));
    setDocs((d) => [...d, { id: nextId++, tokens }]);
    setNextTokens(1500);
  };
  const removeDoc = (id: number) => setDocs((d) => d.filter((x) => x.id !== id));
  const reset = () => {
    setDocs(PRESET);
    nextId = 100;
  };

  return (
    <div className="not-prose my-6 rounded-xl border border-zinc-800 bg-zinc-900 p-4 dark:border-zinc-200 dark:bg-zinc-50">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-zinc-100 dark:text-zinc-900">
            First-Fit Decreasing bin packer
          </p>
          <p className="font-mono text-xs text-zinc-400 dark:text-zinc-500">
            token budget / batch = {budget.toLocaleString()}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <input
            type="number"
            value={nextTokens}
            onChange={(e) => setNextTokens(Number(e.target.value))}
            className="w-24 rounded-md border border-zinc-700 bg-zinc-950 px-2 py-1 text-sm text-zinc-200 dark:border-zinc-300 dark:bg-white dark:text-zinc-900"
            aria-label="tokens for new document"
          />
          <button
            onClick={addDoc}
            className="rounded-md bg-brand-600 px-3 py-1 text-sm font-medium text-white hover:bg-brand-700"
          >
            + doc
          </button>
          <button
            onClick={reset}
            className="rounded-md border border-zinc-700 px-3 py-1 text-sm text-zinc-300 hover:bg-zinc-800 dark:border-zinc-300 dark:text-zinc-600 dark:hover:bg-zinc-100"
          >
            reset
          </button>
        </div>
      </div>

      {overBudget.length > 0 && (
        <p className="mb-3 rounded-md bg-amber-950/40 px-3 py-2 text-xs text-amber-300 dark:bg-amber-50 dark:text-amber-800">
          {overBudget.length} doc(s) exceed the budget and run solo via sliding-window fallback.
        </p>
      )}

      <div className="space-y-2">
        {batches.map((b, i) => {
          const usedPct = (b.used / budget) * 100;
          return (
            <div key={i} className="rounded-lg bg-zinc-950 p-2 dark:bg-white">
              <div className="mb-1 flex justify-between font-mono text-[11px] text-zinc-400 dark:text-zinc-500">
                <span>batch {i + 1}</span>
                <span>
                  {b.used.toLocaleString()} / {budget.toLocaleString()} tokens ·{' '}
                  <span className={usedPct > 90 ? 'text-green-400 dark:text-green-600' : ''}>
                    {Math.round(usedPct)}% full
                  </span>
                </span>
              </div>
              <div className="flex h-7 w-full overflow-hidden rounded bg-zinc-800 dark:bg-zinc-100">
                {b.docs.map((doc, j) => {
                  const w = (doc.tokens / budget) * 100;
                  return (
                    <button
                      key={doc.id}
                      onClick={() => removeDoc(doc.id)}
                      title={`${doc.tokens.toLocaleString()} tokens — click to remove`}
                      aria-label={`${doc.tokens.toLocaleString()} tokens — click to remove`}
                      style={{
                        width: `${w}%`,
                        backgroundColor: PALETTE[j % PALETTE.length],
                      }}
                      className="flex items-center justify-center text-[10px] font-medium text-white transition-opacity hover:opacity-80"
                    >
                      {w > 12 ? doc.tokens.toLocaleString() : ''}
                    </button>
                  );
                })}
                {b.padding > 0 && (
                  <div
                    style={{ width: `${(b.padding / budget) * 100}%` }}
                    className="flex items-center justify-center bg-zinc-800/50 text-[10px] text-zinc-400 dark:bg-zinc-100 dark:text-zinc-500"
                  >
                    {((b.padding / budget) * 100) > 14 ? `pad ${b.padding}` : ''}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>

      <div className="mt-4 grid grid-cols-3 gap-2 text-center">
        <Stat label="batches" value={batches.length.toString()} />
        <Stat label="padding waste" value={`${(totalCap - totalUsed).toLocaleString()} tok`} />
        <Stat
          label="efficiency"
          value={`${efficiency}%`}
          highlight={efficiency >= 85}
        />
      </div>
      <p className="mt-3 text-center text-[11px] text-zinc-400 dark:text-zinc-500">
        Click a document segment to remove it. Padding shrinks as similar-length docs share batches.
      </p>
    </div>
  );
}

function Stat({
  label,
  value,
  highlight,
}: {
  label: string;
  value: string;
  highlight?: boolean;
}) {
  return (
    <div className="rounded-lg bg-zinc-950 px-2 py-2 dark:bg-white">
      <p
        className={`font-mono text-lg font-semibold ${
          highlight ? 'text-green-400 dark:text-green-600' : 'text-zinc-100 dark:text-zinc-900'
        }`}
      >
        {value}
      </p>
      <p className="font-mono text-[10px] uppercase tracking-wide text-zinc-400 dark:text-zinc-500">
        {label}
      </p>
    </div>
  );
}
