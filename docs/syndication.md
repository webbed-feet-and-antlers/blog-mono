# Syndication runbook (POSSE)

How a blog post goes from a merged PR to being published on every platform,
without ever double-posting. POSSE = **P**ublish **O**n your **S**ite,
**S**yndicate **E**verywhere: your site is canonical; everything else is a
copy with a link back.

## The three platform tiers

| Tier | Platforms | Who posts | How |
|---|---|---|---|
| **API** | dev.to, Bluesky, Mastodon, X (via Buffer) | CI, fully automatic | Official/internal APIs with tokens from the `syndication` GitHub environment |
| **Assisted draft** | Medium, Substack, LinkedIn Article, Indie Hackers | Machine drafts, **you** click Publish | Local Playwright sessions (`task posse:login`); CI has no sessions, so there it falls back to tier 3 |
| **Manual package** | same four, as fallback | You, by paste | `.syndication-output/syndicate-<slug>.md` + `.html` artifact with per-platform instructions |

Two sequenced details to know:

- **LinkedIn is two surfaces.** The long-form Article is drafted locally
  (tier 2). The short feed post is its *caption* and deliberately **waits**
  until the Article is published and confirmed — it then shares the
  Article's URL (which LinkedIn renders as a native article card).
- **dev.to updates in place.** Re-running with `--force=true` PUTs the
  article again rather than duplicating it; the social platforms do post
  again, so `--force` is only for genuine re-publishes.

## Publishing a post, start to finish

### 0. One-time local setup (skip if already done)

```sh
cd site && cp .env.example .env        # fill in the API tokens
npm install && npx playwright install chromium

# One browser session per assisted platform (re-run only when one expires):
task posse:login -- substack
task posse:login -- medium
task posse:login -- linkedin
task posse:login -- indiehackers
```

Sessions are Playwright storageState files in
`site/.syndication-output/sessions/` — gitignored, local-only, never in CI.

### 1. Write the post and open a PR

Blog files live in `blogs/*.md(x)`; the filename is the slug. Per-platform
social copy goes in frontmatter (`social.bluesky`, `social.linkedin`, … as
strings or thread arrays); the canonical URL is appended automatically.
`build-check` runs on the PR.

Optional preview of exactly what would be posted, on your PR branch:

```sh
task posse:dry-run:build -- <slug>
```

### 2. Add the `publish` label and merge

```sh
gh pr edit <pr-number> --add-label publish
```

The label is the trigger and the safety: merging **without** it never posts
anything (everyday copy-fix merges are safe). On a labeled merge,
`syndicate-on-merge.yml` runs, posts to the four social platforms + dev.to,
commits the syndication IDs and component screenshots back to main, and
uploads the manual packages as an artifact. **Nothing is posted to Medium,
Substack, LinkedIn Articles, or Indie Hackers from CI** — that's the next
step, done locally.

> Alternative: skip the label and run everything locally after merging with
> `task posse:publish -- <slug>` (posts the API tier + drafts the assisted
> tier in one run). Pick one path per post, not both.

### 3. Draft the no-API platforms (locally, after CI + deploy finish)

Wait for the "Syndicate (on merge)" workflow *and* the Pages deploy to
complete (Medium's import scrapes the live canonical page), then:

```sh
git checkout main && git pull
task posse:assisted -- <slug>
```

This run is safe by construction: platforms CI already posted are skipped
("(already posted)"), the LinkedIn caption waits, and the four no-API
platforms get drafts. Tabs open at each draft editor. Indie Hackers is the
exception — it has no draft state, so a visible editor window opens
pre-filled and the terminal waits while you review.

### 4. Publish each draft, then confirm it

In each tab: review, click **Publish**, and note the public URL. On the
LinkedIn Article also set the canonical URL (⋯ → Settings → Canonical URL
→ your site's URL) so it doesn't cannibalize your SEO. Medium sets the
canonical automatically via the import; the Substack default is a teaser +
link (SEO-safe; `SUBSTACK_DRAFT_MODE=full` switches that).

Then record each publication:

```sh
task posse:confirm -- <slug> substack       https://<pub>.substack.com/p/<post>
task posse:confirm -- <slug> medium         https://medium.com/p/<id>
task posse:confirm -- <slug> linkedinArticle https://www.linkedin.com/pulse/<id>
# Indie Hackers is usually recorded automatically if you clicked Post
# while the assisted window was open; otherwise confirm it too.
```

Confirming moves the link from `draftLinks:` to `syndication:` in the
blog's frontmatter.

### 5. Flush the LinkedIn caption, merge records, and commit

The LinkedIn caption post becomes eligible the moment its Article is
confirmed, but it only goes out on the next syndication run:

```sh
task posse:publish-all      # everything else skips ("already posted");
                            # the waiting LinkedIn caption posts now
```

CI also cannot write syndication IDs back to protected `main` (no CI
credential is granted the ruleset bypass — only your local git credential
is). Publish runs park the IDs + screenshots commit on the
`chore/syndicate-records` branch instead; merge it locally:

```sh
task posse:merge-records    # ff-merges the record branch into main + pushes
git add blogs/<slug>.mdx    # your own confirm/draftLinks changes, if pending
git commit -m "chore(syndicate): confirm assisted posts"
git push
```

The pushed frontmatter is also what renders the "Also published on" footer
on the site (and triggers a rebuild + Pages deploy).

## Why nothing double-posts

- The `publish` label is an explicit, per-PR opt-in.
- A platform ID in the `syndication:` frontmatter means "already posted" —
  every run (local or CI) skips it, including scoped `--blog=<slug>` runs.
- Drafts live in a separate `draftLinks:` block, so a draft suppresses
  re-drafting without ever marking a post as published.
- All CI workflows share the `syndicate` concurrency group (serial, no
  cancel), so two live runs can't overlap.
- The LinkedIn caption can't fire before the Article URL exists.
- The only way to re-post is the explicit `--force=true` flag
  (`npm run syndicate -- --dry-run=false --blog=<slug> --force=true`, or the
  `force` input on the manual workflow dispatch).

## Task + flag reference

| Command | What it does |
|---|---|
| `task posse:dry-run [-- <slug>]` | Preview without posting (add `:build` variant if the post has interactive components) |
| `task posse:publish -- <slug>` | Live: post API tier + draft assisted tier, scoped to one blog |
| `task posse:publish-all` | Live, every blog with anything outstanding |
| `task posse:login -- <platform>` | Capture/refresh a browser session (headed; you handle 2FA) |
| `task posse:assisted -- <slug>` | Same as `posse:manual`: build, run, open draft editors + package |
| `task posse:confirm -- <slug> <platform> <url>` | Record a published draft into `syndication:` |
| `task posse:merge-records` | Merge CI's `chore/syndicate-records` branch (IDs + screenshots) into main |
| `task posse:ci:dry-run` / `posse:ci:publish` | Trigger the GitHub workflow from your terminal |

Key flags to `npm run syndicate`: `--dry-run=` (default true), `--blog=`
(scope; does **not** re-post), `--force=` (re-post/re-draft everything for
the selected blog).

## Troubleshooting

- **"session expired" / login-wall warning in the package** — re-run
  `task posse:login -- <platform>` and re-run `task posse:assisted -- <slug>`.
- **A selector drifted** (Medium/LinkedIn/IH editors change) — the adapter
  fails soft to the manual package with the reason noted in it; paste by
  hand from `.syndication-output/syndicate-<slug>.html` and the
  instructions in the `.md`.
- **Need to re-draft or re-post** — `--force=true` (dev.to updates in
  place; socials will duplicate — usually what you want only for a
  substantially revised post).
- **No local sessions at all** (e.g. on another machine) — download the
  `syndication-packages` artifact from any syndication workflow run; the
  package covers all four manual platforms.
- **Dry-run any time** — `task posse:dry-run -- <slug>` shows the exact
  per-platform plan (thread counts, images, waits) without side effects.
