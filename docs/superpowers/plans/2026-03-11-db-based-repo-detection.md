# DB-Based Repo Detection Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace time-window-based repo detection (`created_at >= cutoff`) with DB-based detection (repo not in DB = new), so the bot catches repos that were private and recently made public.

**Architecture:** Backfill all ~14.6k known repos from `org-repos.csv` into the `repos` table with a `backfilled = TRUE` flag. Change `fetch_recent_repos` to return repos not yet in the DB instead of filtering by `created_at`. Add a `backfilled` column to prevent backfilled repos from ever being posted to BlueSky. For "whale" orgs (those with many repos), fetch more than 10 to avoid missing newly-public repos that aren't in the top 10 by creation date.

**Tech Stack:** Python, SQLite, existing codebase

**Key design decisions:**
- `backfill_source` column (TEXT, nullable) on `repos` table — NULL means organically discovered (eligible for posting), non-NULL means backfilled (e.g. `"org-repos.csv 2026-03-11"`, `"manual 2026-03-11"`)
- `license` column (TEXT) on `repos` table — populated from CSV during backfill and from GitHub API at discovery time
- `get_ready_repos()` adds `AND r.backfill_source IS NULL` to its WHERE clause — this is the safety guard against posting old repos
- Detection: fetch top N repos per org from GitHub API, skip forks, skip any `full_name` already in DB, treat remainder as new
- Whale handling: configurable `per_page` per org (default 10, whales get 100)
- The `--minutes` flag and `CHECK_MINUTES` become unnecessary for detection but are kept for backward compatibility / manual use
- Developer alerting for "old repo made public" scenario (Slack webhook or log-level distinction)

---

## Chunk 1: Backfill Infrastructure

### Task 1: Add `backfill_source` column to schema

**Files:**
- Modify: `open_journalism_bot.py:39-62` (schema + migration)

- [ ] **Step 1: Add `backfill_source` and `license` columns to CREATE TABLE**

In the `repos` CREATE TABLE statement, add after `claude_summary`:
```python
            claude_summary        TEXT,
            license               TEXT,
            backfill_source       TEXT
```

- [ ] **Step 2: Add migration for existing databases**

Add to the `new_columns` list in `init_db()`:
```python
        ("license", "TEXT"),
        ("backfill_source", "TEXT"),
```

Note: SQLite allows NOT NULL with DEFAULT in ALTER TABLE ADD COLUMN — existing rows get the default value.

- [ ] **Step 3: Guard `get_ready_repos()` against backfilled rows**

Modify `get_ready_repos()` (line 133) to exclude backfilled repos:
```python
def get_ready_repos(conn):
    """Get repos that are ready to post (not empty, not yet posted)."""
    return conn.execute(
        """SELECT r.*, o.org_name FROM repos r
           JOIN orgs o ON r.org = o.github_username
           WHERE r.is_empty = 0 AND r.bluesky_post_url IS NULL
                 AND r.backfill_source IS NULL"""
    ).fetchall()
```

- [ ] **Step 4: Test the migration**

Run: `uv run open_journalism_bot.py --dry-run --limit 1`
Expected: Bot starts successfully, no schema errors. Existing repos unaffected.

- [ ] **Step 5: Commit**

```bash
git add open_journalism_bot.py
git commit -m "feat: add backfill_source and license columns, guard posting against backfilled rows"
```

### Task 2: Backfill script

**Files:**
- Create: `backfill_known_repos.py`

This is a one-time-use script to import `org-repos.csv` into the DB. It should be a standalone script (not integrated into the bot's main loop).

- [ ] **Step 1: Write the backfill script**

```python
#!/usr/bin/env python3
"""
One-time backfill: import all repos from org-repos.csv into the database
as 'backfilled' records. These will never be posted to BlueSky but serve
as a baseline for detecting newly-public repos.
"""
import csv
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

def main():
    csv_path = Path(__file__).parent / 'org-repos.csv'
    db_path = Path(__file__).parent / 'data' / 'oj-bot.db'

    if not csv_path.exists():
        print(f"CSV not found: {csv_path}")
        sys.exit(1)
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")

    # Count existing repos for reporting
    existing = conn.execute("SELECT COUNT(*) FROM repos").fetchone()[0]

    imported = 0
    skipped = 0
    missing_org = 0

    with open(csv_path, newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            full_name = row['full_name']
            org = row['org']

            # Skip if repo already exists (discovered by bot or previously backfilled)
            exists = conn.execute(
                "SELECT 1 FROM repos WHERE full_name = ?", (full_name,)
            ).fetchone()
            if exists:
                skipped += 1
                continue

            # Skip if org not in orgs table
            org_exists = conn.execute(
                "SELECT 1 FROM orgs WHERE github_username = ?", (org,)
            ).fetchone()
            if not org_exists:
                missing_org += 1
                continue

            repo_url = f"https://github.com/{full_name}"
            conn.execute(
                """INSERT OR IGNORE INTO repos
                   (full_name, org, repo_name, repo_url, language, description,
                    is_empty, created_at, homepage_url, license, backfill_source)
                   VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)""",
                (
                    full_name,
                    org,
                    row['name'],
                    repo_url,
                    row.get('language') or None,
                    row.get('description') or None,
                    row.get('created_at') or None,
                    row.get('homepage') or None,
                    row.get('license') or None,
                    f"org-repos.csv {datetime.now().strftime('%Y-%m-%d')}",
                ),
            )
            imported += 1

    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM repos").fetchone()[0]
    backfilled_count = conn.execute(
        "SELECT COUNT(*) FROM repos WHERE backfill_source IS NOT NULL"
    ).fetchone()[0]
    conn.close()

    print(f"Before: {existing} repos")
    print(f"Imported: {imported}, Skipped (already in DB): {skipped}, Skipped (org missing): {missing_org}")
    print(f"After: {total} repos ({backfilled_count} backfilled)")


if __name__ == '__main__':
    main()
```

- [ ] **Step 2: Run the orgs sync first (so orgs table is populated)**

Run: `uv run open_journalism_bot.py --dry-run --limit 0`
This fetches the CSV and upserts all orgs without checking any repos.

- [ ] **Step 3: Run the backfill**

Run: `uv run backfill_known_repos.py`
Expected: ~14.6k repos imported with `backfilled = 1`. Existing bot-discovered repos skipped.

- [ ] **Step 4: Verify**

```bash
sqlite3 data/oj-bot.db "SELECT COUNT(*) as total, SUM(CASE WHEN backfill_source IS NOT NULL THEN 1 ELSE 0 END) as backfilled, SUM(CASE WHEN backfill_source IS NULL THEN 1 ELSE 0 END) as discovered FROM repos;"
```
Expected: ~14.6k+ total, ~14.6k backfilled, ~67 discovered.

- [ ] **Step 5: Verify posting guard**

```bash
sqlite3 data/oj-bot.db "SELECT COUNT(*) FROM repos WHERE backfill_source IS NOT NULL AND bluesky_post_url IS NOT NULL;"
```
Expected: 0 (no backfilled repo should ever have a post URL).

- [ ] **Step 6: Commit**

```bash
git add backfill_known_repos.py
git commit -m "feat: one-time backfill script to import org-repos.csv as known repos"
```

---

## Chunk 2: DB-Based Detection

### Task 3: Change `fetch_recent_repos` to return unseen repos

**Files:**
- Modify: `open_journalism_bot.py:301-369` (`fetch_recent_repos` function)
- Modify: `open_journalism_bot.py:852-878` (Phase 1 call site)

The function currently filters by `created_at >= cutoff_time`. Change it to return all non-fork repos from the API response, and let the caller filter against the DB (which it already does via `repo_exists()` at line 866).

- [ ] **Step 1: Rename and simplify `fetch_recent_repos`**

Rename to `fetch_latest_repos`. Remove the `minutes` parameter and the `created_at` filter. Keep `per_page` as a parameter (default 10).

```python
def fetch_latest_repos(github_url, token=None, per_page=10):
    """
    Fetch the most recently created public repos for an org/user.
    Returns list of repo dicts. Does NOT filter by time — caller
    checks against the database for newness.
    Raises RateLimitError if rate limited.
    """
    username = extract_github_username(github_url)
    headers = get_github_headers(token)

    urls_to_try = [
        f'https://api.github.com/users/{username}/repos',
        f'https://api.github.com/orgs/{username}/repos',
    ]

    repos_data = None
    for api_url in urls_to_try:
        try:
            response = requests.get(
                api_url,
                headers=headers,
                params={'sort': 'created', 'direction': 'desc', 'per_page': per_page},
                timeout=30,
            )
            if response.status_code == 200:
                repos_data = response.json()
                break
            elif response.status_code == 403:
                remaining = response.headers.get('X-RateLimit-Remaining', '?')
                if remaining == '0':
                    reset_ts = int(response.headers.get('X-RateLimit-Reset', 0))
                    reset_time = datetime.fromtimestamp(reset_ts, tz=timezone.utc)
                    raise RateLimitError(reset_time)
                continue
            elif response.status_code == 404:
                continue
            else:
                response.raise_for_status()
        except requests.exceptions.HTTPError:
            continue

    if repos_data is None:
        logging.warning(f"Could not fetch repos for {username}")
        return []

    result = []
    for repo in repos_data:
        if repo.get('fork', False):
            continue
        license_info = repo.get('license')
        result.append({
            'repo_name': repo['name'],
            'full_name': repo['full_name'],
            'description': repo.get('description') or '',
            'repo_url': repo['html_url'],
            'language': repo.get('language') or '',
            'created_at': repo['created_at'],
            'homepage': repo.get('homepage') or '',
            'license': license_info.get('name') if license_info else None,
        })

    return result
```

- [ ] **Step 1b: Update `insert_repo` to store license**

Add `license` to the INSERT statement in `insert_repo()` (line ~102), pulling from `repo.get('license')`. Also add it to the function's repo dict handling alongside `homepage`.

- [ ] **Step 2: Update the Phase 1 call site**

At line ~855, change:
```python
        repos = fetch_latest_repos(
            org['github_url'],
            token=config['github_token'],
        )
```

Remove the `minutes=config['check_minutes']` argument.

- [ ] **Step 3: Add logging for "old repo newly public" detection**

In the Phase 1 loop (line ~865-878), after confirming a repo is new (not in DB), add a check: if `created_at` is older than 24 hours, log it at WARNING level as a likely private-to-public conversion.

```python
        for repo in repos:
            if repo_exists(conn, repo['full_name']):
                continue

            # Detect likely private-to-public repos
            created_at = datetime.fromisoformat(repo['created_at'].replace('Z', '+00:00'))
            age_hours = (datetime.now(timezone.utc) - created_at).total_seconds() / 3600
            if age_hours > 24:
                logging.warning(
                    f"{repo['full_name']}: created {created_at.strftime('%Y-%m-%d')} but not previously seen — likely made public recently"
                )

            empty = is_repo_empty(repo)
            # ... rest unchanged
```

- [ ] **Step 4: Update the log message about check window**

At line ~829, change:
```python
    logging.info(f"Checking {len(orgs)} organizations for new repos...")
```
(Remove the "created in last N minutes" phrasing since we're no longer time-filtering.)

- [ ] **Step 5: Test with dry-run**

Run: `uv run open_journalism_bot.py --dry-run --limit 5 --verbose`
Expected: Bot checks 5 orgs, finds 0 new repos (because they're all in the DB now), no errors.

- [ ] **Step 6: Test detection of a "new" repo**

Temporarily remove one repo from the DB and re-run:
```bash
sqlite3 data/oj-bot.db "DELETE FROM repos WHERE full_name = 'ireapps/ire-archive-frontend';"
uv run open_journalism_bot.py --org ireapps --dry-run
```
Expected: Bot discovers `ire-archive-frontend` as new. Then re-insert it afterward or let the dry-run handle it (dry-run uses in-memory DB so the delete persists — re-add it after testing).

Actually, since dry-run copies the disk DB into memory, the delete will affect the in-memory copy. So:
1. Delete the repo from disk DB
2. Run with --dry-run — should detect it as new
3. Run without --dry-run to re-discover and re-insert it (with `--org ireapps`)
4. Verify it's back

- [ ] **Step 7: Commit**

```bash
git add open_journalism_bot.py
git commit -m "feat: switch to DB-based repo detection instead of time-window filtering"
```

### Task 4: Whale org handling

**Files:**
- Modify: `open_journalism_bot.py` (add whale config and pass `per_page` to `fetch_latest_repos`)

- [ ] **Step 1: Define whale orgs**

Add a constant near the top of the file (after imports):
```python
# Orgs with many repos that need deeper fetching to catch newly-public repos.
# Default per_page is 10; whales get 100.
WHALE_ORGS = {
    'abcnews', 'bbcnews', 'nytimes', 'washingtonpost', 'guardian',
    'nprapps', 'propublica', 'datadesk', 'texastribune',
}
```

The exact list should be determined by checking which orgs have the most repos in the CSV:
```bash
cut -d, -f1 org-repos.csv | sort | uniq -c | sort -rn | head -20
```

- [ ] **Step 2: Pass `per_page` based on whale status**

In the Phase 1 loop:
```python
        per_page = 100 if username in WHALE_ORGS else 10
        repos = fetch_latest_repos(
            org['github_url'],
            token=config['github_token'],
            per_page=per_page,
        )
```

- [ ] **Step 3: Test**

Run: `uv run open_journalism_bot.py --org abcnews --dry-run --verbose`
Expected: Fetches 100 repos for abcnews (visible in debug logs), finds 0 new (all in DB).

- [ ] **Step 4: Commit**

```bash
git add open_journalism_bot.py
git commit -m "feat: fetch more repos for whale orgs to catch newly-public repos"
```

---

## Chunk 3: Developer Alerting (Optional / Future)

### Task 5: Developer alerting for unusual discoveries

**This task is a stretch goal. Discuss with the user before implementing.**

The idea: when the bot discovers a repo with `created_at` older than 24h (likely private-to-public), send a push notification so the developer knows something unusual happened. This replaces the need to monitor logs.

**Alerting channel options (pick one):**
1. **Slack Incoming Webhook** — simplest. Add `SLACK_WEBHOOK_URL` to `.env`, POST a JSON payload. No new dependencies (just `requests`).
2. **ntfy.sh** — even simpler. `requests.post('https://ntfy.sh/your-topic', data=message)`. No auth needed (use a hard-to-guess topic name). Install ntfy app on phone to receive. Can also self-host.
3. **Home Assistant** — if you already run HA with the companion app on your phone, use the HA REST API to push notifications. Add `HA_URL` and `HA_TOKEN` to `.env`. No new apps needed.

**Implementation sketch (generic — works with any channel):**

- [ ] **Step 1: Add alerting config to `.env.example`**

```env
# Developer alerting (pick one, leave others blank)
# Slack: incoming webhook URL
ALERT_SLACK_WEBHOOK=
# ntfy.sh: topic URL (e.g. https://ntfy.sh/my-secret-topic)
ALERT_NTFY_TOPIC=
# Home Assistant: base URL and long-lived access token
ALERT_HA_URL=
ALERT_HA_TOKEN=
ALERT_HA_NOTIFY_SERVICE=mobile_app_your_phone
```

- [ ] **Step 2: Add a `send_alert(config, message)` function**

```python
def send_alert(config, message):
    """Send a developer alert via configured channel. Fails silently."""
    try:
        if config.get('alert_slack_webhook'):
            requests.post(config['alert_slack_webhook'], json={'text': message}, timeout=10)
        elif config.get('alert_ntfy_topic'):
            requests.post(config['alert_ntfy_topic'], data=message, timeout=10)
        elif config.get('alert_ha_url') and config.get('alert_ha_token'):
            service = config.get('alert_ha_notify_service', 'notify')
            requests.post(
                f"{config['alert_ha_url']}/api/services/notify/{service}",
                headers={'Authorization': f"Bearer {config['alert_ha_token']}"},
                json={'message': message},
                timeout=10,
            )
    except Exception as e:
        logging.warning(f"Alert failed: {e}")
```

- [ ] **Step 3: Call it from Phase 1 when an old repo is discovered**
- [ ] **Step 4: Also useful for: rate limit errors, connectivity failures, other anomalies**

---

## Cleanup Notes

- `--minutes` / `CHECK_MINUTES` can be kept for backward compat but are no longer used in detection. Consider deprecating with a log warning if set.
- `org-repos.csv` can be `.gitignore`d after backfill if desired — it's a point-in-time snapshot that's now in the DB.
- The backfill script is one-time-use but worth keeping in the repo for documentation.
