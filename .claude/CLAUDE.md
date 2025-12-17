# Claude Code Instructions

## Project Overview

BlueSky bot that posts when journalism organizations create new public GitHub repositories. Spiritual successor to the @newsnerdrepos Twitter bot.

## Running

```bash
# Install dependencies
uv sync

# Test mode (prints to stdout)
uv run open_journalism_bot.py --limit 10

# Test single org
uv run open_journalism_bot.py --org striblab --minutes 1440

# Run for real (set TEST_MODE=false in .env first)
uv run open_journalism_bot.py
```

## Security

- NEVER commit `.env` (contains API credentials)
- `.env.example` has safe placeholder values
- GitHub token only needs "Public Repositories (read-only)" access

## Architecture

- `open_journalism_bot.py` - main script, all logic in one file
- `templates/post.mustache` - BlueSky post template (Mustache format)
- Uses time-based detection: checks repos created within CHECK_MINUTES window
- Link cards are embedded using `atproto` models

## Key Dependencies

- `atproto` - BlueSky API client
- `chevron` - Mustache templating
- `requests` - HTTP requests (GitHub API, CSV fetch)
- `python-dotenv` - Environment variable loading

## Phase 2 TODO

- [ ] GitHub Actions workflow for scheduled runs
- [ ] SQLite database for tracking posted repos (eliminates timing-based duplicate prevention)
- [ ] Thumbnail images in link cards (requires fetching OpenGraph images)
