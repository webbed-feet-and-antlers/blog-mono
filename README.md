# blog-mono

The Inkpens blog monorepo: an Astro site (`site/`) whose content lives in
`blogs/*.md(x)` at the repo root, plus a POSSE syndication pipeline that
cross-posts every published blog to dev.to, Bluesky, Mastodon, X, LinkedIn,
Medium, Substack, and Indie Hackers.

- **Publishing a post** (label a PR, merge, then finish the no-API platforms
  locally): see [docs/syndication.md](docs/syndication.md).
- **Syndication tasks**: `task --list | grep posse` (requires [go-task]).
- **Secrets**: `site/.env.example` documents every variable; CI reads them
  from the `syndication` GitHub environment.

[go-task]: https://taskfile.dev
