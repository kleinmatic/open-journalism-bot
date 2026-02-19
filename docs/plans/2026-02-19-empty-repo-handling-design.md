# Empty Repo Handling Design

**Date:** 2026-02-19
**Status:** Approved, ready for implementation

## Problem

Repos are sometimes detected as "empty" because users create them in the GitHub web UI and push code later. Posting "I think this is an empty repo" is inaccurate and unhelpful — the repo isn't truly empty, we just caught it mid-creation.

## Solution

Hold back empty repos and recheck them hourly for up to 24 hours. If content appears, post with an accurate description. If still empty after 24 hours, abandon silently (don't post).

## Database Schema

SQLite database at `data/oj-bot.db`.

### `orgs` table

| Column          | Type | Notes                          |
|-----------------|------|--------------------------------|
| github_username | TEXT | PRIMARY KEY, e.g., "nytimes"   |
| org_name        | TEXT | Display name                   |
| github_url      | TEXT | Full URL                       |

### `repos` table

| Column           | Type      | Notes                              |
|------------------|-----------|-----------------------------------|
| full_name        | TEXT      | PRIMARY KEY, e.g., "nytimes/covid-data" |
| org              | TEXT      | FK → orgs.github_username         |
| repo_name        | TEXT      | Just the repo part                |
| repo_url         | TEXT      |                                   |
| language         | TEXT      | Nullable                          |
| description      | TEXT      | GitHub's description, nullable    |
| summary          | TEXT      | Claude's summary, nullable        |
| is_empty         | BOOLEAN   |                                   |
| created_at       | TIMESTAMP | GitHub's creation time            |
| first_seen       | TIMESTAMP | When we first detected it         |
| bluesky_post_url | TEXT      | Nullable, filled when posted      |
| bluesky_post_date| TIMESTAMP | Nullable                          |

### State queries

- **Needs posting:** `is_empty = false AND bluesky_post_url IS NULL`
- **Pending recheck:** `is_empty = true AND bluesky_post_url IS NULL AND first_seen > now - 24h`
- **Abandoned:** `is_empty = true AND first_seen < now - 24h` (never posted)
- **Posted:** `bluesky_post_url IS NOT NULL`

## Bot Flow

Each hourly run:

1. **Sync orgs from CSV** — Fetch CSV, upsert into `orgs` table
2. **Check for new repos** — For each org, fetch recent repos; skip any already in `repos` table; insert new repos
3. **Handle empty repos** — If `is_empty = true`, don't post yet
4. **Recheck pending empty repos** — Query pending repos, re-fetch description/README; if content found, update `is_empty = false`; if still empty and past 24h, leave as abandoned
5. **Post ready repos** — Query ready repos, post to BlueSky, update `bluesky_post_url` and `bluesky_post_date`
6. **Log stats** — "Posted X repos, Y pending, Z abandoned this run"

## Configuration

- Database: `data/oj-bot.db` (created automatically on first run)
- Add `data/` to `.gitignore`
- No new environment variables

## CLI Changes

- `--dry-run`: No BlueSky posts, **no database writes**. Logs what would happen.
- `--db PATH`: Use alternate database path (for testing)

## Error Handling

- **Rate limit mid-run:** Safe to exit; next run picks up where we left off
- **Repo deleted:** Treat 404 on recheck as abandoned
- **Post fails:** Leave `bluesky_post_url` null; next run retries

## Testing

- pytest as test framework (dev dependency)
- Use in-memory `:memory:` database for test isolation
- Existing `--dry-run` and `--org` flags for manual testing
- New `--db` flag for testing with throwaway database

## Future Use Cases (Out of Scope)

- Weekly summary: "This week news orgs posted X repos" — query by `bluesky_post_date`
- Monthly "most stars" — fetch stars on-demand via API for repos posted in last month
