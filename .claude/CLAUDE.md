# Claude Code Instructions

## Project Overview

BlueSky bot that posts when journalism organizations create new public GitHub repositories. Spiritual successor to the @newsnerdrepos Twitter bot.

## Running

```bash
# Install dependencies
uv sync

# Test mode (prints to stdout instead of posting)
# Use --dry-run to force test mode regardless of TEST_MODE in .env
uv run open_journalism_bot.py --limit 10 --dry-run

# Test single org (--dry-run prevents actual posting)
uv run open_journalism_bot.py --org striblab --minutes 1440 --dry-run

# Run for real (requires TEST_MODE=false in .env)
uv run open_journalism_bot.py
```

**Note:** If `TEST_MODE=false` in `.env`, the bot will post to BlueSky. Always use `--dry-run` when testing to avoid accidental posts.

## Security

- NEVER commit `.env` (contains API credentials)
- `.env.example` has safe placeholder values
- GitHub token only needs "Public Repositories (read-only)" access
- Anthropic API key is optional but enables AI-generated descriptions

## Architecture

- `open_journalism_bot.py` - main script, all logic in one file
- `templates/post.mustache` - BlueSky post template (Mustache format)
- `logs/bot.log` - rotating log file (5MB, 3 backups)
- Uses time-based detection: checks repos created within CHECK_MINUTES window
- Link cards are embedded using `atproto` models
- Description tiers: GitHub description (in card) → Claude README summary → language fallback → "empty repo" (imputed descriptions go in post body, not card)

## Debugging

```bash
# Debug with verbose logging and large time window to find historical repos
uv run open_journalism_bot.py --org <org> --minutes 10000000 --dry-run --verbose

# Check logs
tail -f logs/bot.log
```

## Environment Notes

- No `jq` installed - use Python for JSON parsing: `curl ... | python3 -c "import sys,json; ..."`
- GitHub Events API (`/orgs/{org}/events`) strips PushEvent commit data — do NOT use for counting commits
- Use Search Commits API instead: `gh api search/commits -X GET -f 'q=org:<org> committer-date:<start>..<end>' -f 'per_page=1' --jq '.total_count'` (30 req/min rate limit)

## Key Dependencies

- `anthropic` - Claude API for README summarization
- `atproto` - BlueSky API client
- `chevron` - Mustache templating
- `requests` - HTTP requests (GitHub API, CSV fetch)
- `python-dotenv` - Environment variable loading

## Database

SQLite at `data/oj-bot.db` with two tables:
- `orgs` — columns: `github_username` (PK), `org_name` (NOT NULL), `github_url` (NOT NULL)
- `repos` — FK to `orgs.github_username` via `org` column. Key columns: `full_name` (PK), `is_empty`, `bluesky_post_url`, `bluesky_post_date`, `claude_summary`, `earliest_commit_date`, `homepage_url`, `committer_login`, `committer_name`, `committer_bio`

Empty repos are held back and rechecked hourly for up to 24h. `--dry-run` is fully side-effect-free (no DB writes). `--db PATH` for testing with alternate database.

## Metadata & Summaries

At discovery time (Phase 1), the bot collects repo metadata (`earliest_commit_date`, `homepage_url`, committer info) and generates a `claude_summary` from the README. The posting loop (Phase 3) is NOT modified — it uses the same description tiers as before.

Backfill scripts (uncommitted utilities):
- `backfill_metadata.py` — populate metadata/summaries for repos already in DB
- `backfill_from_bluesky.py` — scrape bot's BlueSky history to insert pre-SQLite repos

## Newsletter Summaries

`/repo-summaries <date range>` skill reads from SQLite and generates summaries to `summaries/` (git-ignored). Falls back to WebFetch for repos missing `claude_summary`. Also includes a "most active orgs" top 10 by public commit count via Search Commits API.

## Phase 2 TODO

- [ ] GitHub Actions workflow for scheduled runs
- [x] SQLite database for tracking posted repos
- [x] Repo metadata and Claude summaries at discovery time
- [ ] Thumbnail images in link cards (requires fetching OpenGraph images)
