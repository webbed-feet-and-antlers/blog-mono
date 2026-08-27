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

## Running — four tiers

```sh
task study-app:evals-prepare   # one-time dataset download (see below)
task study-app:evals-gate      # fast tier (~10 min): every suite at EVALS_N=3
task study-app:evals           # full run (~1h): every suite at EVALS_N=10
task study-app:evals-heldout   # rare held-out check on the test split
```

Or directly: `uv run python -m evals.data` then `uv run pytest evals/ -m evals`.

**What to run when:**
- **Iterating on one feature** — run just its suite (fastest inner loop):
  `uv run pytest evals/suites/test_quiz.py -m evals`
- **After any product change** — `task study-app:evals-gate`. Small-N cases are
  a seeded *subset* of the full run's cases, so gate numbers roll up coherently
  into full-run trends. Means over 3 cases are noisier — a marginal gate
  failure warrants a full run before concluding regression.
- **Before promoting baselines** — `task study-app:evals` (the full N=10 run
  on the val split), then an `evals-heldout` pass to confirm the numbers
  generalize beyond the tuning sample.

## Splits & overfitting

Every dataset big enough is split once at prepare time into **train / val /
test** pools (seeded, disjoint; the replay datasets split by whole user so no
learner spans a split). Suites draw from the `EVALS_SPLIT` pool — `val` by
default.

Nothing in this harness fits parameters: the generation suites drive prompt-
based LLM calls, FSRS runs its default (externally-fit) parameters, and the
recommendation replay exercises an untrained policy. The overfitting risk is
therefore not parametric — it's **tuning prompts and gate thresholds against
the same fixed sample until it passes**. The splits catch exactly that:

- **train** — scratch pool for exploratory runs (`EVALS_SPLIT=train`), and the
  correct pool for any future fitting (FSRS parameters, bandit weights — the
  fsrs suite already estimates its constant baseline here).
- **val** — the everyday pool: gate/full runs and every committed baseline.
  Promoting a non-val run is refused by `python -m evals.report --promote`.
- **test** — held out. `task study-app:evals-heldout` runs it rarely; its
  results are never tuned against and never promoted. A healthy system shows
  val ≈ test; a val-vs-test gap means the features (or the gates) were tuned
  to the val sample.

AL-CPL is 4 courses — too small to split, so it ships as one pool used by
every split (there is no held-out prerequisite-graph check).

Prepared pools are capped at prepare time (SciQ 150, RACE 200, notes 120,
Duolingo 120 users, EdNet 60 users) — the suites draw at most ~25 cases per
run, so every split pool stays far larger than the draw. The full HF cache
(a few GB) is only needed while preparing; afterwards it can be deleted, and
`evals/data/manifest.json` (sha256 per file) verifies the prepared data
without re-downloading.

Knobs:
- `EVALS_N` caps cases per suite (default 10). Results live in
  `evals/reports/<run>/`, the rendered table in `evals/EVALS.md` (with
  per-suite runtime + split), and each run's numbers are diffed against the
  committed `evals/reports/baselines/`.
- `EVALS_SPLIT` picks the dataset split (train/val/test, default val).
- `EVALS_CONCURRENCY` (default 4) caps in-flight LLM calls. The generation
  suites run their chains and judge calls concurrently — the tools are pure
  async functions with no DB — which is what keeps a full run around half an
  hour instead of an evening. Raise it if your OpenRouter limits allow.
- Promote the latest run to the new baseline after an intentional change:
  `uv run python -m evals.report --promote` (commit `reports/baselines/`).
  Promotion copies per-suite files, so it *merges*: re-running one suite and
  promoting refreshes only that suite's baseline. Note `reports/latest/`
  holds only the most recent pytest run's suites — after a partial re-run,
  copy the untouched suites' JSONs from the last full run's timestamped
  directory into `latest/` first, or EVALS.md and the baselines will
  silently lose them.
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

1. **`retrievability` was the wrong formula — FIXED (2026-08).** The wrapper
   computed exp(−t/S); FSRS's memory model is a power law. On real Duolingo
   traces the exponential was catastrophically miscalibrated (Brier ~0.28–0.6
   vs ~0.08 for the power law) — fine as a ranking key, wrong as the recall
   probability the dashboard shows. The wrapper now delegates to the
   library's `get_card_retrievability`; the suite gates the wrapper against
   the library curve plus absolute calibration bars (Brier ≤ 0.12, log-loss
   ≤ 0.50) so neither a hand-rolled formula nor silent drift can return.
   (`fsrs` suite.)
2. **Time-decay does not beat item difficulty on this data.** FSRS ranking
   sits within sampling noise of the last-outcome streak (|gap| ≤ 0.006
   across draws at n≈1k, AUC SE ≈0.02 — superiority was never a defensible
   gate), and a simple running correct-rate baseline outranks the forgetting
   curve on Duolingo material. Parameters were fit on Anki data; study-app
   material is not vocabulary. Recorded as findings; the gate is a
   collapse floor (AUC > 0.51). (`fsrs` suite.)
3. **Reflection narratives contradicted their grounding packet — FIXED
   (2026-08).** On the strong-but-neglectful archetype the generator claimed
   "has not reviewed any flashcards" over eight flashcard activities (mean
   faithfulness 0.53). The layer was reworked: the packet renders as
   labeled sections instead of dense JSON, the prompt carries explicit
   count/absolute-claiming rules with a contrastive example, and a
   temperature-0 self-verify pass drops or corrects unsupported claims (a
   failed verify keeps the first pass). The faithfulness gate rose from a
   0.45 floor to 0.60. (`reflection` suite.)
4. **Passage→difficulty inference has no signal.** Grading
   `analyze_document`'s difficulty field against RACE's middle/high tiers
   scored 0.40 — below the ~0.50 majority-class baseline. The tiers label
   the *questions*; the passage alone apparently doesn't carry them.
   Report-only. (`analysis` suite.)
5. **Notes go generic on some papers.** Key-point coverage vs the expert
   abstract averages ~0.74 but hits 0.0 on papers where the notes
   discussed the field instead of the study's aim/methods/findings.
   `generate_notes` now demands the document's own aim/methods/findings
   before field background. (`notes` suite; ROUGE vs the abstract is
   report-only — good notes restructure and simplify, so lexical overlap
   is structurally low.)
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
8. **Renamed files lost their material type.** The descriptiveness judge
   scored names like "Cellular Respiration" as half-done — "identifies the
   topic but does not clearly specify the material type". Fixed in
   production: `suggest_filename` now requires topic **plus** material
   type (lecture notes, chapter summary, problem set…), and prefers a
   distinguishing subtopic when the content supports one. (`rename` suite.)
9. **Free-tier empty-response storms outlast linear backoff.** Under full
   -N concurrency, OpenRouter's free tier occasionally returns empty
   responses long enough to exhaust the old 1s/2s retry window (a planner
   case died this way while passing standalone). `chat()` now backs off
   exponentially (1/2/4/8s across five attempts) and the planner suite
   sleeps between outer attempts. (`app/llm.py`, `evals/suites/test_planner.py`.)
10. **The recommender's weakness targeting improved — lift is still
    negative.** The original engine ordered its due-review deck arbitrarily
    (macro lift −0.03 to −0.06: the "weak" concepts it surfaced failed
    *less* often next than random ones). `DueReviewReadyStrategy` and the
    session composer now rank due concepts by predicted failure risk —
    running correct-rate blended with the power-law forgetting curve (the
    two signals this harness validated; equal weights justified on the
    train split, not tuned against val). Engine precision rose from
    ~0.05 to ~0.07 and the lift gap roughly halved, but random targeting
    (~0.11 failure precision) still wins. The residual cause: lifetime
    correct-rate goes stale for improving learners (early struggles keep a
    concept "weak" forever), and EdNet re-exposure is platform-driven, not
    FSRS-driven — fixing it properly needs per-concept recent-outcome
    tracking, which the replay (which builds its own mastery data) can't
    even exercise. Still macro-averaged and report-only; the structural
    gates (never-empty primary, due backlog keeps review on the slate)
    remain gated. (`recommend` suite.)
11. **Planner plans front-loaded zero review.** 3 of 5 plans scheduled no
    review item in the first days despite due concepts (weak_engaged_early
    0.40 vs the 0.60 gate) — the prompt's "interleave review" rule was
    unenforced. Fixed in production, mirroring the minute-cap pattern: a
    prompt MUST-rule for a day-0/1 review item, one regeneration when it's
    violated, and a deterministic day-0 injection targeting the due
    concepts when the LLM still won't comply. (`planner` suite.)

Judge-scored metrics gate on the run's **mean**, not per-case: judge scores
are stochastic, and a single harsh (or numerically erratic — judges
occasionally return out-of-range values; those are retried once and then
skipped, never clamped to 0) verdict shouldn't fail an otherwise-good run.
Deterministic metrics (structure, invariants, calibration) stay per-case.
