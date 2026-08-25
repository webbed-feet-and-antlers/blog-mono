---
name: interactive-blog-component
description: Build interactive React island components embedded in Astro + MDX blogs on this blog (theinkpens / blog-mono). Use when asked to create, build, add, or scaffold an interactive component, demo, widget, or MDX island for a blog — e.g. "build an interactive X for the blog", "add a demo component", "create a Y visualizer", or any mention of `client:visible`, `site/src/components/react/`, or an embedded interactive demo. Encodes the dark-first theming convention, the not-prose wrapper, the dependency-free rule, accessibility requirements, and POSSE screenshot-harness registration.
---

# Interactive Blog Component

Create interactive React islands embedded in blogs (`<Component client:visible />` in MDX). These are the only place client-side JavaScript runs on this static-first site, so they must follow strict conventions to stay consistent, accessible, and screenshot-able for cross-posting.

## CRITICAL: dark-first theming (the #1 bug)

This site inverts Tailwind's `dark:` variant. Read this before writing a single class:

- **Bare classes = DARK mode** (the default; no class on `<html>`)
- **`dark:` prefixed classes = LIGHT mode** (applied when `.light` is on `<html>`)

So `dark:` does **not** mean dark mode here — it means *light* mode. Every color must be authored as a pair: `<bare=dark> dark:<light>`. Forgetting the `dark:` half is the single most common mistake, producing a "stuck on dark" element in light mode.

Example: card background → `bg-zinc-900 dark:bg-zinc-50`. Primary text → `text-zinc-100 dark:text-zinc-900`.

The full class-pair table (card chrome, panels, borders, text tiers, brand accent, phase fills) is in **[references/component-spec.md](references/component-spec.md)** — read it when choosing colors.

## Build workflow

1. **Copy the template:** `cp assets/template.tsx src/components/react/<Name>.tsx` (from the `site/` dir). It has the correct wrapper, hooks, dark-first pairs, and a11y patterns.
2. **Name it `PascalCase`.** The filename **must equal** the component tag and the harness `KNOWN` entry: `BinPacker.tsx` → `<BinPacker />` → `/sshot/BinPacker/dark`. Default-export the component.
3. **Hand-roll visuals — no charting libraries.** Import only React hooks (`useEffect`/`useMemo`/`useRef`/`useState`). Use inline SVG or flexbox. The whole point of islands is tiny JS; recharts/d3 bloats the per-blog bundle.
4. **Author every color as a dark/light pair** per the table in `references/component-spec.md`. Bare = dark, `dark:` = light. Audit each class for its partner before finishing.
5. **Root `<div>` must start with** `not-prose my-6 rounded-xl border border-zinc-800 bg-zinc-900 p-4 dark:border-zinc-200 dark:bg-zinc-50`. The `not-prose` is load-bearing (the blog body is wrapped in Tailwind Typography `prose`, which otherwise rewrites your buttons/inputs/SVGs).
6. **Accessibility:** `role="img"` + descriptive `aria-label` on every meaningful SVG/chart; `aria-label` on icon-only or collapsed-text buttons; `<label>` wrappers on inputs; `focus-visible:ring-*` on interactive controls. See `references/component-spec.md` §5.
7. **Use `font-mono`** for labels, stats, and parameter readouts (the "instrument panel" look); sans only for the card title. See §10.
8. **Register for screenshots (4 edits):** to be screenshot-able for POSSE cross-posting, the component must be added in four places in `src/pages/sshot/[component]/[theme].astro` — import, `KNOWN` in `getStaticPaths`, page-scope `KNOWN`, and a `{component === 'Name' && <Name client:only="react" />}` render branch. See `references/component-spec.md` §7 for the exact pattern and *why* (Astro's `client:only` compiler needs a static import).
9. **Embed in the blog:** `import Name from '../site/src/components/react/Name'` then `<Name client:visible />` in the MDX body.
10. **Verify:** run `scripts/verify.sh <Name>` (from `site/`). It audits dark/light pairing, checks `not-prose`, flags charting-library imports, and confirms harness registration. Fix everything it reports.

## Resources

- **[references/component-spec.md](references/component-spec.md)** — the full spec: exact class-pair table, card chrome, accessibility patterns, the 4-place harness checklist with rationale, the Tailwind literal-class rule, state-management patterns. Read when you need the concrete class strings.
- **[assets/template.tsx](assets/template.tsx)** — a minimal correct starting component. Copy it and fill in the logic.
- **[scripts/verify.sh](scripts/verify.sh)** — automated rule-checking. Run after building; it catches the common bugs (missing `dark:` partner, unregistered in harness, etc.).

## Quick checklist (before finishing)

- [ ] Filename = component tag = harness `KNOWN` entry; default export
- [ ] Imports only React hooks (no recharts/d3)
- [ ] Root div has `not-prose ... border border-zinc-800 bg-zinc-900 ... dark:border-zinc-200 dark:bg-zinc-50`
- [ ] Every color class has its dark/light partner
- [ ] SVGs have `role="img"` + `aria-label`; icon buttons have `aria-label`; inputs are labeled; controls have `focus-visible` rings
- [ ] `font-mono` on labels/stats; Tailwind colors are full literals (never `bg-${x}-500`)
- [ ] Harness edited in all 4 places
- [ ] Embedded in blog with `<Name client:visible />`
- [ ] `scripts/verify.sh <Name>` passes
