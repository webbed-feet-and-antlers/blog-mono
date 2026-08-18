# Evals — measuring the AI features against public datasets

Every suite exercises the **real production code** (`app.agent.tools`,
`app.agent.planner`, `app.agent.reflection`, `app.agent.fsrs_scheduler`,
`app.recommend.engine`, the session-composer route) — never a
reimplementation — so the scores track the product. Public datasets supply
gold labels where gold exists; where none exists (flashcards, plans,
reflections), suites pair deterministic invariants with an LLM judge running
on a **dedicated stronger model** (`EVALS_JUDGE_MODEL`, temperature 0) so a
model never grades its own failure modes.

No users needed: learner-dependent features are evaluated by **replay** —
real Duolingo review traces through our FSRS wrapper, real EdNet
interaction logs through the recommendation engine.

## Running — three tiers

```sh
task study-app:evals-prepare   # one-time dataset download (see below)
task study-app:evals-gate      # fast tier (~10 min): every suite at EVALS_N=3
task study-app:evals           # full run (~30 min): every suite at EVALS_N=10
```

Or directly: `uv run python -m evals.data` then `uv run pytest evals/ -m evals`.

**What to run when:**
- **Iterating on one feature** — run just its suite (fastest inner loop):
  `uv run pytest evals/suites/test_quiz.py -m evals`
- **After any product change** — `task study-app:evals-gate`. Small-N cases are
  a seeded *subset* of the full run's cases, so gate numbers roll up coherently
  into full-run trends. Means over 3 cases are noisier — a marginal gate
  failure warrants a full run before concluding regression.
- **Before promoting baselines** — `task study-app:evals` (the full N=10 run).

Knobs:
- `EVALS_N` caps cases per suite (default 10). Results live in
  `evals/reports/<run>/`, the rendered table in `evals/EVALS.md` (with
  per-suite runtime), and each run's numbers are diffed against the committed
  `evals/reports/baselines/`.
- `EVALS_CONCURRENCY` (default 4) caps in-flight LLM calls. The generation
  suites run their chains and judge calls concurrently — the tools are pure
  async functions with no DB — which is what keeps a full run around half an
  hour instead of an evening. Raise it if your OpenRouter limits allow.
- Promote the latest run to the new baseline after an intentional change:
  `uv run python -m evals.report --promote` (commit `reports/baselines/`).
- The suites are stochastic (generation at temperature 0.2–0.4, judges at
  temperature 0): a single marginal failure usually warrants a re-run; the
  report keeps every number either way.
- The deterministic suites (`fsrs`, `recommend`, `session`) make no LLM calls
  and run in minutes; `planner` and `reflection` touch the DB and stay
  sequential.

## Suites

| Suite | Feature under test | Public dataset | Headline metrics |
|---|---|---|---|
| `analysis` | `analyze_document` | AL-CPL (concepts + prerequisite pairs, docs composed from Wikipedia summaries); RACE (difficulty tiers) | concept F1, prerequisite-edge recall (report-only), difficulty accuracy, summary faithfulness (judge) |
| `quiz` | analyze → plan → `generate_quiz` | SciQ (gold MCQs + expert distractors) | structural pass, groundedness, distractor plausibility, concept-tag accuracy, personalization shift (novice vs advanced) |
| `flashcards` | analyze → plan → `generate_flashcards` | SciQ passages | structural pass, variant distinctness, quality rubric (judge), application-style shift |
| `notes` | `generate_notes` | PubMed long docs + human abstracts | markdown structure, ROUGE-1 vs abstract, DeepEval faithfulness, key-point coverage (judge) |
| `rename` | `suggest_filename` + heuristic gate | SciQ passages with synthetic noise filenames | gate recall, rule pass, descriptiveness (judge) |
| `fsrs` | FSRS scheduling + `retrievability` | Duolingo HLR (13M real review traces) | AUC / Brier / log-loss vs constant, streak, running-rate baselines |
| `planner` | `generate_study_plan` | synthetic modules seeded from SciQ | plan invariants (daily load, horizon, weak-first), rationale-cites-evidence (judge) |
| `recommend` | `engine.decide` | EdNet-KT1 (real learner logs) | empty-primary rate, due-backlog slate coverage, weakness precision vs random |
| `reflection` | `reflect_on_learner` | synthetic behavior ledgers (3 archetypes) | numbers-grounded fraction, format clamps, insight faithfulness (judge) |
| `session` | study-session composer | synthetic mastery states | mix ratios by accuracy band, most-forgotten-first, pool fallbacks, scope filter |

## Datasets & licenses (research use)

Prepared into `evals/data/` (gitignored); large upstream files stay in the
Hugging Face cache.

| Dataset | Source | License/terms |
|---|---|---|
| SciQ | HF `allenai/sciq` (Welbl et al. 2017) | CC BY-SA 3.0 / research |
| RACE | HF `ehovy/race` (Lai et al. 2017) | research use |
| AL-CPL | github.com/harrylclc/AL-CPL-dataset (Liang et al. 2018) | research use |
| PubMed summarization | HF `ccdv/pubmed-summarization` (Cohan et al.) | research use |
| Duolingo HLR traces | HF `Zai04/Duolingo-Spaced-Repetition-Data` (mirror of Settles et al. 2016, settles.acl16) | MIT (data release) |
| EdNet-KT1 | HF `mgor/EDNet` (mirror of Riiid's EdNet, Choi et al. 2020) | CC BY-NC 4.0 |

## Findings the first calibration run surfaced

These are recorded in the reports (some as explicit `finding_*` metrics) and
are the honest starting point for improving the product:

1. **`retrievability` is the wrong formula.** The app computes
   exp(−t/S); FSRS's memory model is a power law. On real Duolingo traces
   the exponential is catastrophically miscalibrated (Brier ~0.28–0.6 vs
   ~0.08 for the power law) — fine as a ranking key, wrong as the recall
   probability the dashboard shows. (`fsrs` suite.)
2. **Time-decay does not beat item difficulty on this data.** FSRS ranking
   beats chance and the last-outcome streak, but a simple running
   correct-rate baseline outranks the forgetting curve on Duolingo
   material — and the recommender's due-concept targeting shows zero lift
   over random at predicting next failures on EdNet. Parameters were fit
   on Anki data; study-app material is not vocabulary. (`fsrs`, `recommend`.)
3. **Reflection narratives contradict their grounding packet.** On the
   strong-but-neglectful archetype the generator claimed "has not reviewed
   any flashcards" over eight flashcard activities. The faithfulness gate
   is set at 0.45 as a regression floor, not an aspiration — improve the
   reflection layer, then raise the bar. (`reflection` suite.)
4. **Passage→difficulty inference has no signal.** Grading
   `analyze_document`'s difficulty field against RACE's middle/high tiers
   scored 0.40 — below the ~0.50 majority-class baseline. The tiers label
   the *questions*; the passage alone apparently doesn't carry them.
   Report-only. (`analysis` suite.)
5. **Notes go generic on some papers.** Key-point coverage vs the expert
   abstract averages ~0.74 but hits 0.0 on papers where the notes
   discussed the field instead of the study's aim/methods/findings.
   (`notes` suite; ROUGE vs the abstract is report-only — good notes
   restructure and simplify, so lexical overlap is structurally low.)
6. **Planner days ran over budget.** The prompt promises ≤45min/day but
   nothing enforced it — the suite caught 68–75-minute days. Fixed in
   production: `generate_study_plan` now regenerates once when the
   heaviest day exceeds a 60-minute cap, keeping the lighter plan.
   (`planner` suite — the invariant that caught it stays gated.)
7. **The planner judged its own homework — the harness had a scale bug.**
   GEval's judge prompt asks for an integer score 0–10 (normalized /10)
   and its example JSON literally demonstrates `"score": 0`. Criteria
   text that described a "0.0–1.0" scale fought the template: judges
   following the criteria emitted 1 (=0.1 after normalization); judges
   anchoring on the example emitted 0 while writing positive reasons.
   Criteria must describe anchors *qualitatively* ("top of the scale =
   fully covered"), and `judge_score()` retries/skips unparseable
   verdicts instead of clamping them to 0. (`evals/suites/__init__.py`.)

Judge-scored metrics gate on the run's **mean**, not per-case: judge scores
are stochastic, and a single harsh (or numerically erratic — judges
occasionally return out-of-range values; those are retried once and then
skipped, never clamped to 0) verdict shouldn't fail an otherwise-good run.
Deterministic metrics (structure, invariants, calibration) stay per-case.
