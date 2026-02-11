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

## Key Dependencies

- `anthropic` - Claude API for README summarization
- `atproto` - BlueSky API client
- `chevron` - Mustache templating
- `requests` - HTTP requests (GitHub API, CSV fetch)
- `python-dotenv` - Environment variable loading

## Phase 2 TODO

- [ ] GitHub Actions workflow for scheduled runs
- [ ] SQLite database for tracking posted repos (eliminates timing-based duplicate prevention)
- [ ] Thumbnail images in link cards (requires fetching OpenGraph images)
