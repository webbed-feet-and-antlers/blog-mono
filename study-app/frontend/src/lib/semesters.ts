/**
 * Semester organization helpers — shared by the Modules page, the new-module
 * modal, and the file-to-module prompt.
 *
 * Canonical term order within an academic year: Autumn/Fall first, then
 * Spring, then Summer. Unknown labels sort after, alphabetically.
 */

import type { Module } from "../types";

export const TERM_ORDER: Record<string, number> = {
  autumn: 0,
  fall: 0,
  spring: 1,
  summer: 2,
};

export function yearStartOf(academicYear: string | null | undefined): number {
  const n = parseInt((academicYear ?? "").slice(0, 4), 10);
  return Number.isNaN(n) ? 0 : n;
}

export function termOrderOf(term: string | null | undefined): number {
  return TERM_ORDER[(term ?? "").toLowerCase()] ?? 3;
}

/** The academic year containing today, as "2026/27". */
export function currentAcademicYear(): string {
  const now = new Date();
  // Academic years roll over around August — before then it's still the
  // previous year's cycle (e.g. July 2026 → "2025/26").
  const start = now.getMonth() >= 7 ? now.getFullYear() : now.getFullYear() - 1;
  return `${start}/${String(start + 1).slice(2)}`;
}

/** Default semester for a NEW module: the latest semester already in use,
 * else the current calendar academic year with no term chosen. */
export function defaultSemesterFor(modules: Module[]): {
  academic_year: string | null;
  term: string | null;
} {
  const assigned = modules.filter((m) => m.academic_year);
  if (assigned.length === 0) {
    return { academic_year: currentAcademicYear(), term: null };
  }
  assigned.sort(
    (a, b) =>
      yearStartOf(b.academic_year) - yearStartOf(a.academic_year) ||
      termOrderOf(b.term) - termOrderOf(a.term),
  );
  return {
    academic_year: assigned[0].academic_year ?? null,
    term: assigned[0].term ?? null,
  };
}

/** Year options for pickers: years seen on modules ∪ current year ± 1. */
export function buildYearOptions(modules: Module[]): string[] {
  const years = new Set<string>([currentAcademicYear()]);
  const cur = yearStartOf(currentAcademicYear());
  years.add(`${cur - 1}/${String(cur).slice(2)}`);
  years.add(`${cur + 1}/${String(cur + 2).slice(2)}`);
  for (const m of modules) {
    if (m.academic_year) years.add(m.academic_year);
  }
  return [...years].sort((a, b) => yearStartOf(b) - yearStartOf(a));
}

export interface SemesterModuleGroup {
  key: string;
  label: string;
  modules: Module[];
  isCurrent: boolean;
  isUnsorted: boolean;
}

/** Group modules by (academic_year, term), newest first. The first group is
 * the current semester; unassigned modules form a trailing group. */
export function groupModulesBySemester(
  modules: Module[],
): SemesterModuleGroup[] {
  const byKey = new Map<string, SemesterModuleGroup>();
  for (const m of modules) {
    const hasSemester = !!m.academic_year || !!m.term;
    const key = hasSemester
      ? `${m.academic_year ?? ""}·${m.term ?? ""}`
      : "__unsorted__";
    if (!byKey.has(key)) {
      byKey.set(key, {
        key,
        label: hasSemester
          ? [m.term, m.academic_year].filter(Boolean).join(" ")
          : "No semester set",
        modules: [],
        isCurrent: false,
        isUnsorted: !hasSemester,
      });
    }
    byKey.get(key)!.modules.push(m);
  }
  const groups = [...byKey.values()];
  groups.sort((a, b) => {
    if (a.isUnsorted) return 1;
    if (b.isUnsorted) return -1;
    return (
      yearStartOf(b.modules[0]?.academic_year) -
        yearStartOf(a.modules[0]?.academic_year) ||
      termOrderOf(b.modules[0]?.term) - termOrderOf(a.modules[0]?.term)
    );
  });
  const first = groups.find((g) => !g.isUnsorted);
  if (first) first.isCurrent = true;
  return groups;
}
