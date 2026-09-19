// Word-level diffing: LCS over lines first, then words within changed
// line pairs — cheap for blog-post-sized documents.

export type DiffPart = { type: "same" | "add" | "del"; text: string };

export type DiffRow =
  | { type: "same"; text: string }
  | { type: "del"; text: string }
  | { type: "add"; text: string }
  | { type: "mod"; a: string; b: string; parts: DiffPart[] };

function lcs<T>(a: T[], b: T[]): number[][] {
  const n = a.length;
  const m = b.length;
  const dp: number[][] = Array.from({ length: n + 1 }, () =>
    new Array<number>(m + 1).fill(0),
  );
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] =
        a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  return dp;
}

function splitWords(s: string): string[] {
  return s.match(/\S+\s*/g) ?? [];
}

export function wordDiff(a: string, b: string): DiffPart[] {
  const A = splitWords(a);
  const B = splitWords(b);
  const dp = lcs(A, B);
  const parts: DiffPart[] = [];
  const push = (type: DiffPart["type"], text: string) => {
    const last = parts[parts.length - 1];
    if (last && last.type === type) last.text += text;
    else parts.push({ type, text });
  };
  let i = 0;
  let j = 0;
  while (i < A.length && j < B.length) {
    if (A[i] === B[j]) {
      push("same", A[i]);
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      push("del", A[i]);
      i++;
    } else {
      push("add", B[j]);
      j++;
    }
  }
  while (i < A.length) push("del", A[i++]);
  while (j < B.length) push("add", B[j++]);
  return parts;
}

export function lineDiff(a: string, b: string): DiffRow[] {
  const A = a.split("\n");
  const B = b.split("\n");
  const dp = lcs(A, B);
  const raw: DiffRow[] = [];
  let i = 0;
  let j = 0;
  while (i < A.length && j < B.length) {
    if (A[i] === B[j]) {
      raw.push({ type: "same", text: A[i] });
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      raw.push({ type: "del", text: A[i] });
      i++;
    } else {
      raw.push({ type: "add", text: B[j] });
      j++;
    }
  }
  while (i < A.length) raw.push({ type: "del", text: A[i++] });
  while (j < B.length) raw.push({ type: "add", text: B[j++] });

  // Pair adjacent del/add runs into modified rows with word-level detail.
  const rows: DiffRow[] = [];
  let k = 0;
  while (k < raw.length) {
    if (raw[k].type !== "del") {
      rows.push(raw[k]);
      k++;
      continue;
    }
    const dels: DiffRow[] = [];
    while (k < raw.length && raw[k].type === "del") dels.push(raw[k++]);
    const adds: DiffRow[] = [];
    while (k < raw.length && raw[k].type === "add") adds.push(raw[k++]);
    const pairs = Math.min(dels.length, adds.length);
    for (let p = 0; p < pairs; p++) {
      rows.push({
        type: "mod",
        a: (dels[p] as { text: string }).text,
        b: (adds[p] as { text: string }).text,
        parts: wordDiff((dels[p] as { text: string }).text, (adds[p] as { text: string }).text),
      });
    }
    rows.push(...dels.slice(pairs), ...adds.slice(pairs));
  }
  return rows;
}

/** Collapse long runs of unchanged rows into context windows. */
export function collapsedRows(rows: DiffRow[], context = 2): (DiffRow | { type: "gap"; count: number })[] {
  const keep = new Array<boolean>(rows.length).fill(false);
  rows.forEach((r, idx) => {
    if (r.type !== "same") {
      for (let k = Math.max(0, idx - context); k <= Math.min(rows.length - 1, idx + context); k++) {
        keep[k] = true;
      }
    }
  });
  const out: (DiffRow | { type: "gap"; count: number })[] = [];
  let gap = 0;
  rows.forEach((r, idx) => {
    if (keep[idx]) {
      if (gap > 0) {
        out.push({ type: "gap", count: gap });
        gap = 0;
      }
      out.push(r);
    } else {
      gap++;
    }
  });
  if (gap > 0) out.push({ type: "gap", count: gap });
  return out;
}
