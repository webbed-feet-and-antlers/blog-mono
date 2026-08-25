# Interactive Blog Component — Full Spec

Source of truth: the components at `src/components/react/` (`BinPacker.tsx`, `LearningRateScheduler.tsx`, `RenderingStrategies.tsx`; `Chart.tsx` is a legacy recharts wrapper, not a template), the harness `src/pages/sshot/[component]/[theme].astro`, `src/styles/global.css`, and `src/layouts/Blog.astro`.

## Table of contents
1. File location & naming
2. Dark-first theming — exact class pairs
3. The `not-prose` wrapper & card chrome
4. Dependency-free rule
5. Accessibility requirements
6. `client:visible` embedding in MDX
7. Screenshot harness registration (4-place checklist + why)
8. State management patterns
9. Brand palette & phase fills
10. Typography / fonts
11. Button / pill / stat / bar patterns

---

## 1. File location & naming

- **Location:** `src/components/react/`
- **Naming:** `PascalCase.tsx`, one default-exported component per file. The filename **must equal** the component tag used in MDX and the `component` segment in the screenshot harness: `BinPacker.tsx` → `<BinPacker ... />` → `/sshot/BinPacker/dark`. The harness `KNOWN` set and the `getStaticPaths` regex `/<([A-Z][A-Za-z0-9]+)\b[^>]*\/?>/g` both depend on this exact correspondence.
- **Default export required** (not named): `export default function BinPacker(...)`. MDX imports it as `import BinPacker from '...'`.

---

## 2. Dark-first theming — exact class pairs

Defined in `global.css`:
```css
@custom-variant dark (&:where(.light, .light *));
```
**`dark:` matches when `.light` is on `<html>` — i.e. `dark:` = LIGHT mode.** Bare classes are the DARK default. Author every color as `<bare=dark> dark:<light>`.

| Surface | Bare (DARK) | `dark:` (LIGHT) |
|---|---|---|
| Card background | `bg-zinc-900` | `dark:bg-zinc-50` |
| Card border | `border-zinc-800` | `dark:border-zinc-200` |
| Inner panel / chart well / stat cell | `bg-zinc-950` | `dark:bg-white` |
| Inner border / ring | `border-zinc-700` / `ring-zinc-800` | `dark:border-zinc-300` / `dark:ring-zinc-200` |
| Bar / track background | `bg-zinc-800` | `dark:bg-zinc-100` |
| Padding zone | `bg-zinc-800/50` | `dark:bg-zinc-100` |
| Primary text (titles) | `text-zinc-100` | `dark:text-zinc-900` |
| Secondary text (subtitles) | `text-zinc-400` | `dark:text-zinc-500` |
| Muted / axis text | `text-zinc-500` | `dark:text-zinc-400` |
| Secondary button border | `border-zinc-700` | `dark:border-zinc-300` |
| Secondary button text | `text-zinc-300` | `dark:text-zinc-600` |
| Secondary button hover bg | `hover:bg-zinc-800` | `dark:hover:bg-zinc-100` |
| Brand accent (text) | `text-brand-400` | `dark:text-brand-600` |
| Brand accent (marker/legend) | `bg-brand-300` / `text-brand-300` | `dark:bg-brand-400` / `dark:text-brand-600` |
| "Good value" green | `text-green-400` | `dark:text-green-600` |
| Amber warning text | `text-amber-300` | `dark:text-amber-800` |
| Amber warning bg | `bg-amber-950/40` | `dark:bg-amber-50` |

**Focus ring offset is also dark-first:** `focus-visible:ring-offset-zinc-950 dark:focus-visible:ring-offset-white`.

> The single most common bug: forgetting the `dark:` half → a "stuck on dark" element in light mode. Audit every color class for its partner. `scripts/verify.sh` checks this automatically.

---

## 3. The `not-prose` wrapper & card chrome

Components render inside `.prose` (see `Blog.astro`: the `<slot/>` is wrapped in `prose prose-invert prose-zinc ...`). The Typography plugin rewrites descendant typography, so without opting out your buttons/inputs/SVGs inherit prose styles (oversized fonts, weird spacing, the backtick pseudo-elements on `<code>`).

**Standard root wrapper (identical in all hand-rolled components):**
```tsx
<div className="not-prose my-6 rounded-xl border border-zinc-800 bg-zinc-900 p-4 dark:border-zinc-200 dark:bg-zinc-50">
```
- `not-prose` — opt out of Typography (load-bearing; `global.css` also keys inline-code styling off `:not(.not-prose)`)
- `my-6` — vertical rhythm matching a paragraph
- `rounded-xl border ... p-4` — the card chrome

**Inner panels / chart wells:** `rounded-lg bg-zinc-950 p-2 dark:bg-white` (or `p-3`). One step more rounded-down (`rounded-lg` vs outer `rounded-xl`) and one step darker (`zinc-950` vs `zinc-900`).

**Stat cells:** `rounded-lg bg-zinc-950 px-2 py-2 dark:bg-white`, arranged in `grid grid-cols-3 gap-2 text-center`.

---

## 4. Dependency-free rule

`BinPacker`, `LearningRateScheduler`, `RenderingStrategies` ship **zero** charting/runtime deps. Import only React hooks:
```tsx
import { useMemo, useState } from 'react';        // BinPacker, LearningRateScheduler
import { useEffect, useMemo, useRef, useState } from 'react';  // RenderingStrategies
```

Visuals are hand-rolled:
- **Inline SVG** (`<svg viewBox>` + `<polyline>`/`<line>`/`<text>`) — LearningRateScheduler
- **Flexbox bars** (`flex h-7 w-full`, each segment a `<div>`/`<button>` with `width: '%'`) — BinPacker, RenderingStrategies

**Why:** island JS size is the architecture's selling point (blogs sell "most readers get zero JavaScript"). recharts/d3 would bloat the per-blog bundle. `Chart.tsx` is a legacy recharts wrapper, **not a template** — new components follow the three hand-rolled ones.

---

## 5. Accessibility requirements

- **`role="img"` + descriptive `aria-label` on every meaningful SVG/chart:**
  ```tsx
  <svg role="img" aria-label={`Learning rate over training steps using the ${active.label} schedule`}>
  ```
  For a flexbox "chart", put it on the container `<div role="img" aria-label={...}>`.
- **`aria-label` on icon-only / collapsed-text buttons** (also keep a `title` for mouse hover):
  ```tsx
  <button aria-label={`${doc.tokens.toLocaleString()} tokens — click to remove`} title="...">
  ```
- **Focus-visible ring on interactive controls:**
  ```tsx
  className="... focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 focus-visible:ring-offset-zinc-950 dark:focus-visible:ring-offset-white"
  ```
- **Labeled inputs:** wrap in `<label className="block">` with a visible `<span>` caption; or use `aria-label` on a standalone input.
- **`aria-hidden="true"`** on purely decorative swatches/icons.
- **Disabled state** communicated, not just visual: `disabled={x}` + `disabled:cursor-not-allowed disabled:opacity-50`.

---

## 6. `client:visible` embedding in MDX

Blogs live outside the app at `blogs/*.md(x)`. Imports use a relative path up and into the app:
```mdx
import BinPacker from '../site/src/components/react/BinPacker';
import RenderingStrategies from '../site/src/components/react/RenderingStrategies';
```
Then embedded in the body:
```mdx
<RenderingStrategies client:visible />
<BinPacker client:visible budget={8192} />
```
- `client:visible` = hydrate lazily when scrolled into view. Props can be passed.
- The harness uses `client:only="react"` instead (different directive — see §7).

**Inline code in MDX containing JSX-looking tags** (e.g. `` `<BinPacker client:visible />` ``) renders correctly as literal text in `<code>` — do not HTML-escape it (escaping causes double-escaping).

---

## 7. Screenshot harness registration (4-place checklist + why)

File: `src/pages/sshot/[component]/[theme].astro`. To make a component screenshot-able for POSSE cross-posting, edit **four** places. There is no auto-discovery for the render step.

**Why explicit branches are required:** Astro's compiler needs a *static* import reference for every `client:only` island — it cannot bundle a variable component reference.

**What IS auto-derived (do NOT touch):** `getStaticPaths` walks `blogs/**/*.{md,mdx}`, regex-finds component tags actually used, and emits a page per `component × theme` — but only for names in `KNOWN`. A component not in `KNOWN` just won't get screenshotted (it won't break the build).

The four edits when adding `NewThing`:

**(a) Import** (alongside the others):
```tsx
import NewThing from '../../../components/react/NewThing';
```

**(b) `KNOWN` inside `getStaticPaths`** (drives path generation):
```tsx
const KNOWN = new Set(['BinPacker', 'Chart', 'LearningRateScheduler', 'RenderingStrategies', 'NewThing']);
```

**(c) Page-scope `KNOWN`** (a duplicate — Astro hoists `getStaticPaths` to its own scope and frontmatter consts aren't visible there; keep the two in sync):
```tsx
const KNOWN = new Set(['BinPacker', 'Chart', 'LearningRateScheduler', 'RenderingStrategies', 'NewThing']);
```

**(d) Render branch:**
```tsx
{component === 'NewThing' && <NewThing client:only="react" />}
```
Directive is `client:only="react"` (skips SSR — the harness only mounts the hydrated client). If the component takes props, supply representative ones.

**Also load-bearing:** `import '../../../styles/global.css';` must stay — without it the `@custom-variant dark` isn't defined and the `.light` root class won't switch themes, so light screenshots render as dark.

---

## 8. State management patterns

Only React built-in hooks — no zustand/redux/context.

- **`useState`** for inputs: plain primitives (`useState<T[]>(INIT)`, `useState(1500)`, `useState<ScheduleId>('cosine')`, `useState(true)`).
- **`useMemo`** for derived data — always memoize the recomputed model:
  ```tsx
  const batches = useMemo(() => firstFitDecreasing(docs, budget), [docs, budget]);
  ```
- **`useEffect`** only for animation (e.g. a `requestAnimationFrame` loop driven by a `playing` boolean), with `useRef<number | null>` for the RAF handle and a cleanup calling `cancelAnimationFrame`.
- **Functional updates** that branch on current value: `setProgress(p => ...)`, `setCompare(c => !c)`.
- **Pure helpers live OUTSIDE the component** (e.g. `firstFitDecreasing`, `lrAt`, `phaseBounds`) — take args, return values, no hooks. Keeps them testable.

---

## 9. Brand palette & phase fills

**Brand ("Ink" indigo)** — `global.css` `@theme`: `--color-brand-50` `#eef2ff` → `--color-brand-950` `#1e1b4b`. Used stops: `brand-400 #818cf8`, `brand-500 #6366f1`, `brand-600 #4f46e5`, `brand-700 #4338ca`.
- Primary buttons: `bg-brand-600 ... hover:bg-brand-700` (or `hover:bg-brand-500`)
- Selected tab: `bg-brand-600 text-white`
- Range inputs: `accent-brand-600`
- SVG curve stroke: `stroke-brand-500`

**Tailwind literal-class rule (CRITICAL):** never construct class names by interpolation (`bg-${color}-500`). The compiler must see full literal strings. For per-role colors, assign each to a const:
```tsx
const SERVER = 'bg-emerald-500';   // server compute
const NETWORK = 'bg-sky-500';      // network transfer
const JSEXEC = 'bg-fuchsia-500';   // client JS execution
```
For arbitrary hex colors, use inline `style={{ backgroundColor: '#1e5cf5' }}` (escapes the JIT-detection rule):
```tsx
const PALETTE = ['#1e5cf5', '#16a34a', '#ea580c', '#9333ea', '#db2777', '#0891b2'];
```

---

## 10. Typography / fonts

Fonts (`global.css` `@theme`): `--font-sans: "Inter"`, `--font-serif: "Fraunces"`, `--font-mono: "JetBrains Mono"`.

**Mono is the language for labels and stats** (the "instrument panel" look, visually separating the island from serif blog prose):
- Subtitle / parameter readout: `font-mono text-xs text-zinc-400 dark:text-zinc-500`
- Per-row metadata: `font-mono text-[11px]` or `text-[10px]`
- Stat values: `font-mono text-lg font-semibold`
- Stat labels: `font-mono text-[10px] uppercase tracking-wide`
- Card title (the one non-mono piece): `text-sm font-semibold text-zinc-100 dark:text-zinc-900` (sans)

**Inside SVG:** set `fontSize` and `fontFamily="ui-monospace, monospace"` as **attributes**, not Tailwind classes (classes don't reach inside SVG reliably):
```tsx
<text fontSize={9} fontFamily="ui-monospace, monospace" className="fill-zinc-400 dark:fill-zinc-500">
```

---

## 11. Button / pill / stat / bar patterns

**Primary button:**
```tsx
className="rounded-md bg-brand-600 px-3 py-1 text-sm font-medium text-white transition-colors hover:bg-brand-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 focus-visible:ring-offset-zinc-950 dark:focus-visible:ring-offset-white"
```

**Secondary button / inactive tab:**
```tsx
className="rounded-md border border-zinc-700 px-3 py-1 text-sm text-zinc-300 hover:bg-zinc-800 dark:border-zinc-300 dark:text-zinc-600 dark:hover:bg-zinc-100"
```

**Active pill/tab:** `rounded-md px-2.5 py-1 text-xs font-medium bg-brand-600 text-white` (with `aria-pressed={isActive}`).

**Stat cell:**
```tsx
<div className="rounded-lg bg-zinc-950 px-2 py-2 dark:bg-white">
  <p className="font-mono text-lg font-semibold text-zinc-100 dark:text-zinc-900">{value}</p>
  <p className="font-mono text-[10px] uppercase tracking-wide text-zinc-400 dark:text-zinc-500">{label}</p>
</div>
```

**Bar / track:**
```tsx
<div className="flex h-9 w-full overflow-hidden rounded-md ring-1 ring-inset ring-zinc-800 dark:ring-zinc-200">
  {/* segments with width: '%' + fill class */}
</div>
```
