# CLAUDE.md

## What this repo is

The BlueSky discovery bot for `openjournalism.news`. Runs hourly on a small dedicated host. Watches the news-org GitHub accounts listed in `silva-shih/open-journalism`'s `orgs.csv`, posts new repos to BlueSky, and enriches them with Claude-generated summaries and AI-coding signals before storing in the canonical SQLite at `data/oj-bot.db`.

## The canonical copy lives on the production host, not in your local clone

This local checkout is for **git operations and reading source**. The running bot — and the production SQLite — live on the host (see the operator's private notes for hostname and access). Two consequences:

- **Edit on the host**, not in your local checkout. If you mount the host filesystem (e.g. via sshfs), edit through the mount; otherwise `scp` files over. Editing here and pushing without syncing the host first is how you lose work.
- **Run tests on the host**: `ssh <host> "cd ~/Code/open-journalism-bot && uv run pytest tests/ -v"`.
- **Git operations on the host**: pull before push (the host's remote may be ahead). Push with agent forwarding if your GitHub key only lives on your laptop: `ssh -A <host> "cd ~/Code/open-journalism-bot && git push"`.

## When to run claude here

- Bot internals: discovery loop, BlueSky posting, hourly enrichment, the production `ai_signals.py` module
- Backfill scripts (`backfill_new_orgs.py`, `backfill_from_bluesky.py`, `backfill_metadata.py`)
- Anything that runs *on the production host* as part of the bot's cron

For biweekly post drafting, the local-only `ai_signals.py` scanner (laptop-side, uses `gh api`), WordPress publishing, or commit-count reporting, the maintainer uses a separate private hub repo.

## Key facts

- Canonical DB: `data/oj-bot.db` on the production host (one DB, one location — never copy)
- Bot cron runs hourly on the host via `uv`
- SQLite `org` column stores **lowercase** GitHub usernames — always use lowercase in queries
- Discovery pipeline runs `enrich_ai_signals()` on every newly-discovered non-empty repo and after a successful empty-repo recheck — this is the module that lives in this repo (uses `requests`, no `gh` CLI required)
- Bot logs live at `logs/bot.log` on the host; a nightly summary is emailed via the host's MTA

## No-scoop policy: do not post unpublished stories

The bot exists to celebrate open-work culture, **not** to scoop journalists with their own code. Never post a repo that may back an unpublished story. The clearest, most frequent offender is The Pudding.

### The Pudding (`the-pudding`) — hold scaffolding for unpublished stories

The Pudding routinely creates **public** GitHub repos months before a story publishes. Auto-posting these announces an in-progress story before its authors do — a no-scoop violation — even when the repo's code looks finished.

**Rule (implemented):** The bot never auto-posts `the-pudding` repos. `get_ready_repos()` excludes the org outright (`AND r.org != 'the-pudding'`). Their repos are still recorded in the DB at discovery — they just never get tweeted in real time. They enter the biweekly digest manually, once the story is confirmed live on **pudding.cool**.

**Do NOT resurrect the `homepage`-field heuristic.** Verified 2026-05-31: published stories (`kpop-generations`, `similes`, `ivf`, `happy-map`, `pockets`) all have a **null** `homepage`, while several repos that *do* set it are non-story infrastructure (`website`, `svelte-starter`, `data`). The field tracks nothing useful here.

**The biweekly "is it live?" check — the RSS feed.** `https://pudding.cool/feed.xml` lists every published story with its canonical URL (e.g. `https://pudding.cool/2026/05/kpop-generations`), and the repo slug appears in the path. So a Pudding repo is live iff some feed `<link>` ends with the repo name. Secondary signal that it's *not* ready: placeholder copy in `src/data/copy.json` (`"Title TK"`, `"Description tk."`, `"TK TK"`).

## Related repo

- `silva-shih/open-journalism` (public, shared) — the upstream `orgs.csv` this bot reads. PRs go upstream; never push directly to master.
