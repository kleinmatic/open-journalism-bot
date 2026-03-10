# Repo Metadata & Claude Summary Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collect repo metadata (earliest commit date, homepage URL, committer info) and a 3-5 sentence Claude summary at bot runtime, so the `/repo-summaries` skill can read from SQLite instead of hitting the GitHub API.

**Architecture:** Add new columns to the `repos` table. Collect metadata and generate the Claude summary at insert time (Phase 1 discovery), when the repo is first seen. The existing posting flow (Phase 3) is not modified at all. The `/repo-summaries` skill is then simplified to just query the database and format output.

**Tech Stack:** Python, SQLite, requests, anthropic SDK, GitHub REST API

---

## Chunk 1: Schema & Metadata Collection

### Task 1: Add new columns to the repos table

**Files:**
- Modify: `open_journalism_bot.py:38-55` (schema in `init_db`)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_db.py`:

```python
def test_schema_has_metadata_columns(db):
    """New metadata columns exist in the repos table."""
    row = db.execute("PRAGMA table_info(repos)").fetchall()
    col_names = [r[1] for r in row]
    assert "earliest_commit_date" in col_names
    assert "homepage_url" in col_names
    assert "committer_login" in col_names
    assert "committer_name" in col_names
    assert "committer_bio" in col_names
    assert "claude_summary" in col_names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_db.py::test_schema_has_metadata_columns -v`
Expected: FAIL — columns don't exist yet

- [ ] **Step 3: Add columns to schema**

In `init_db`, add these columns to the `CREATE TABLE IF NOT EXISTS repos` statement, after `bluesky_post_date`:

```python
            earliest_commit_date TIMESTAMP,
            homepage_url     TEXT,
            committer_login  TEXT,
            committer_name   TEXT,
            committer_bio    TEXT,
            claude_summary   TEXT
```

Also add a migration block **before** the `CREATE INDEX` statement to handle existing databases:

```python
        -- Migration: add metadata columns if missing
        ALTER TABLE repos ADD COLUMN earliest_commit_date TIMESTAMP;
        ALTER TABLE repos ADD COLUMN homepage_url TEXT;
        ALTER TABLE repos ADD COLUMN committer_login TEXT;
        ALTER TABLE repos ADD COLUMN committer_name TEXT;
        ALTER TABLE repos ADD COLUMN committer_bio TEXT;
        ALTER TABLE repos ADD COLUMN claude_summary TEXT;
```

Note: SQLite `ALTER TABLE ADD COLUMN` silently succeeds if the column already exists in newer versions, but to be safe, wrap each in a try/except or use `executescript` with error handling. The simplest approach: run each ALTER individually via `conn.execute()` inside a try/except block, outside the `executescript` call.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_db.py::test_schema_has_metadata_columns -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add open_journalism_bot.py tests/test_db.py
git commit -m "schema: add metadata and claude_summary columns to repos table"
```

### Task 2: Fetch metadata at discovery time

**Files:**
- Modify: `open_journalism_bot.py` (add `fetch_repo_metadata` function and call it from Phase 1)
- Test: `tests/test_db.py`

The GitHub repos list API (`/users/{user}/repos`) already returns `created_at` and `homepage` per repo. We already extract `created_at` at line 335. For `homepage`, we just need to add it to the dict built in `fetch_recent_repos`.

For **earliest commit date**, we need a new API call to the commits endpoint. This is the only expensive one — it requires paginating to the last page. We do this once per repo at discovery.

For **committer info**, we need two API calls: get most recent commit's author login, then get that user's profile for name/bio.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_db.py`:

```python
def test_insert_repo_with_metadata(db):
    """insert_repo stores metadata fields."""
    _seed_org(db)
    repo = {
        "full_name": "testorg/metarepo",
        "repo_name": "metarepo",
        "repo_url": "https://github.com/testorg/metarepo",
        "language": "Python",
        "description": "A test repo",
        "homepage": "https://testorg.github.io/metarepo",
    }
    metadata = {
        "earliest_commit_date": "2025-06-15T10:00:00Z",
        "committer_login": "jdoe",
        "committer_name": "Jane Doe",
        "committer_bio": "Journalist & developer",
    }
    insert_repo(db, repo, org_username="testorg", is_empty=False, metadata=metadata)
    row = db.execute("SELECT * FROM repos WHERE full_name='testorg/metarepo'").fetchone()
    assert row["homepage_url"] == "https://testorg.github.io/metarepo"
    assert row["earliest_commit_date"] == "2025-06-15T10:00:00Z"
    assert row["committer_login"] == "jdoe"
    assert row["committer_name"] == "Jane Doe"
    assert row["committer_bio"] == "Journalist & developer"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_db.py::test_insert_repo_with_metadata -v`
Expected: FAIL — `insert_repo` doesn't accept `metadata` param yet

- [ ] **Step 3: Update `insert_repo` to accept and store metadata**

Modify `insert_repo` to accept an optional `metadata=None` parameter. When provided, store the fields:

```python
def insert_repo(conn, repo, org_username, is_empty=False, metadata=None):
    """Insert a new repo into the database."""
    meta = metadata or {}
    conn.execute(
        """INSERT OR IGNORE INTO repos
           (full_name, org, repo_name, repo_url, language, description, is_empty, created_at,
            homepage_url, earliest_commit_date, committer_login, committer_name, committer_bio)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            repo["full_name"],
            org_username,
            repo["repo_name"],
            repo["repo_url"],
            repo.get("language") or None,
            repo.get("description") or None,
            is_empty,
            repo.get("created_at"),
            repo.get("homepage") or None,
            meta.get("earliest_commit_date"),
            meta.get("committer_login"),
            meta.get("committer_name"),
            meta.get("committer_bio"),
        ),
    )
    conn.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_db.py::test_insert_repo_with_metadata -v`
Expected: PASS

- [ ] **Step 5: Run all existing tests to check nothing broke**

Run: `uv run pytest tests/ -v`
Expected: All existing tests still pass (they don't pass `metadata`, which defaults to `None`)

- [ ] **Step 6: Commit**

```bash
git add open_journalism_bot.py tests/test_db.py
git commit -m "feat: insert_repo accepts optional metadata dict"
```

### Task 3: Add `fetch_repo_metadata` function

**Files:**
- Modify: `open_journalism_bot.py` (new function)
- Test: `tests/test_db.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_db.py`:

```python
from unittest.mock import patch, MagicMock


def test_fetch_repo_metadata_basic(db):
    """fetch_repo_metadata returns earliest commit, committer info."""
    from open_journalism_bot import fetch_repo_metadata

    # Mock the repo API (for homepage — already in repo dict, so this is for earliest commit)
    mock_commits_response = MagicMock()
    mock_commits_response.status_code = 200
    mock_commits_response.headers = {}  # no Link header = single page
    mock_commits_response.json.return_value = [
        {"commit": {"author": {"date": "2025-08-01T12:00:00Z"}},
         "author": {"login": "jdoe"}},
        {"commit": {"author": {"date": "2025-06-15T10:00:00Z"}},
         "author": {"login": "jdoe"}},
    ]

    mock_user_response = MagicMock()
    mock_user_response.status_code = 200
    mock_user_response.json.return_value = {
        "name": "Jane Doe",
        "bio": "Journalist & developer",
    }

    def mock_get(url, **kwargs):
        if "/commits" in url:
            return mock_commits_response
        if "users/jdoe" in url:
            return mock_user_response
        return MagicMock(status_code=404)

    with patch("open_journalism_bot.requests.get", side_effect=mock_get):
        meta = fetch_repo_metadata("testorg/myrepo", token=None)

    assert meta["earliest_commit_date"] == "2025-06-15T10:00:00Z"
    assert meta["committer_login"] == "jdoe"
    assert meta["committer_name"] == "Jane Doe"
    assert meta["committer_bio"] == "Journalist & developer"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_db.py::test_fetch_repo_metadata_basic -v`
Expected: FAIL — `fetch_repo_metadata` doesn't exist

- [ ] **Step 3: Implement `fetch_repo_metadata`**

Add to `open_journalism_bot.py`, near the other fetch functions:

```python
def fetch_repo_metadata(full_name, token=None):
    """
    Fetch metadata for a repo: earliest commit date, most recent committer info.
    Returns a dict with keys: earliest_commit_date, committer_login, committer_name, committer_bio.
    All values may be None if the API call fails or data is unavailable.
    """
    headers = get_github_headers(token)
    meta = {
        "earliest_commit_date": None,
        "committer_login": None,
        "committer_name": None,
        "committer_bio": None,
    }

    # Fetch commits — newest first (GitHub default, only sort available)
    commits_url = f"https://api.github.com/repos/{full_name}/commits"
    try:
        response = requests.get(
            commits_url, headers=headers,
            params={"per_page": 100}, timeout=30,
        )
        if response.status_code != 200:
            return meta

        commits = response.json()
        if not commits:
            return meta

        # Most recent committer (first in list)
        author = commits[0].get("author") or {}
        meta["committer_login"] = author.get("login")

        # Earliest commit: check if there are more pages
        link_header = response.headers.get("Link", "")
        if 'rel="last"' in link_header:
            import re
            match = re.search(r'page=(\d+)>; rel="last"', link_header)
            if match:
                last_page = int(match.group(1))
                last_response = requests.get(
                    commits_url, headers=headers,
                    params={"per_page": 100, "page": last_page}, timeout=30,
                )
                if last_response.status_code == 200:
                    last_commits = last_response.json()
                    if last_commits:
                        meta["earliest_commit_date"] = last_commits[-1]["commit"]["author"]["date"]
        else:
            # All commits fit on one page — earliest is the last item
            meta["earliest_commit_date"] = commits[-1]["commit"]["author"]["date"]

        # Fetch committer profile for name/bio
        if meta["committer_login"]:
            user_url = f"https://api.github.com/users/{meta['committer_login']}"
            user_response = requests.get(user_url, headers=headers, timeout=30)
            if user_response.status_code == 200:
                user_data = user_response.json()
                meta["committer_name"] = user_data.get("name") or None
                meta["committer_bio"] = user_data.get("bio") or None

    except requests.exceptions.RequestException as e:
        logging.warning(f"{full_name}: metadata fetch failed: {e}")

    return meta
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_db.py::test_fetch_repo_metadata_basic -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add open_journalism_bot.py tests/test_db.py
git commit -m "feat: add fetch_repo_metadata for earliest commit and committer info"
```

### Task 4: Wire metadata collection into Phase 1 discovery

**Files:**
- Modify: `open_journalism_bot.py:697-727` (Phase 1 loop)
- Modify: `open_journalism_bot.py:329-336` (`fetch_recent_repos` — add `homepage` to returned dict)

- [ ] **Step 1: Add `homepage` to repo dict in `fetch_recent_repos`**

At line 336, the repo dict is built. Add `homepage`:

```python
            new_repos.append({
                'repo_name': repo['name'],
                'full_name': repo['full_name'],
                'description': repo.get('description') or '',
                'repo_url': repo['html_url'],
                'language': repo.get('language') or '',
                'created_at': repo['created_at'],
                'homepage': repo.get('homepage') or '',
            })
```

- [ ] **Step 2: Call `fetch_repo_metadata` and pass to `insert_repo` in Phase 1**

In the Phase 1 loop (around line 719-720), after `is_repo_empty` and before `insert_repo`:

```python
            empty = is_repo_empty(repo)
            metadata = fetch_repo_metadata(repo['full_name'], token=config['github_token'])
            insert_repo(conn, repo, org_username=username, is_empty=empty, metadata=metadata)
```

- [ ] **Step 3: Run all tests**

Run: `uv run pytest tests/ -v`
Expected: All pass. The integration test mocks `requests.get`, so the new API calls in `fetch_repo_metadata` will hit the mock — verify this doesn't cause issues. If it does, the integration test's mock may need to handle the new URL patterns.

- [ ] **Step 4: Manual smoke test**

Run: `uv run open_journalism_bot.py --org mtfreepress --minutes 1440000 --dry-run --verbose`

Check logs for metadata fetch activity. Verify the repo row in the DB has populated metadata columns:

```bash
sqlite3 data/oj-bot.db "SELECT full_name, earliest_commit_date, homepage_url, committer_login, committer_name FROM repos WHERE earliest_commit_date IS NOT NULL LIMIT 5;"
```

- [ ] **Step 5: Commit**

```bash
git add open_journalism_bot.py
git commit -m "feat: collect repo metadata at discovery time"
```

## Chunk 2: Claude Summary Generation

### Task 5: Add `generate_claude_summary` function

**Files:**
- Modify: `open_journalism_bot.py` (new function near `summarize_with_claude`)
- Test: `tests/test_db.py`

This is a second Claude call with a longer, newsletter-oriented prompt. It runs at discovery time (Phase 1), completely independent of the existing posting flow.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_db.py`:

```python
def test_generate_claude_summary(db):
    """generate_claude_summary returns a 3-5 sentence summary."""
    from open_journalism_bot import generate_claude_summary

    mock_message = MagicMock()
    mock_message.content = [MagicMock(text="This project analyzes data. It uses Python and pandas. Built by a newsroom for investigative journalism.")]

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_message

    with patch("open_journalism_bot.anthropic.Anthropic", return_value=mock_client):
        result = generate_claude_summary("# My Project\nThis analyzes data.", "fake-key")

    assert result is not None
    assert len(result) > 50
    assert "BOILERPLATE" not in result


def test_generate_claude_summary_boilerplate(db):
    """generate_claude_summary returns None for boilerplate."""
    from open_journalism_bot import generate_claude_summary

    mock_message = MagicMock()
    mock_message.content = [MagicMock(text="BOILERPLATE")]

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_message

    with patch("open_journalism_bot.anthropic.Anthropic", return_value=mock_client):
        result = generate_claude_summary("# Getting Started\nRun npm install", "fake-key")

    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_db.py::test_generate_claude_summary tests/test_db.py::test_generate_claude_summary_boilerplate -v`
Expected: FAIL — function doesn't exist

- [ ] **Step 3: Implement `generate_claude_summary`**

Add near `summarize_with_claude` in `open_journalism_bot.py`:

```python
def generate_claude_summary(readme_content, api_key):
    """
    Generate a 3-5 sentence newsletter summary of a repo from its README.
    Returns the summary string, or None if the README is boilerplate/inappropriate.
    """
    if not api_key:
        return None

    client = anthropic.Anthropic(api_key=api_key)

    prompt = """Summarize this GitHub repository in 3-5 sentences for a newsletter about open source journalism tools. Cover what the project does, why it matters, and what technologies it uses.

If the README is just boilerplate/template content (auto-generated placeholder text, empty of real content, or just installation instructions with no project description), respond with exactly: BOILERPLATE

If the README contains inappropriate content, respond with exactly: INAPPROPRIATE

README content:
"""

    try:
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=512,
            messages=[
                {"role": "user", "content": prompt + readme_content[:8000]}
            ]
        )
        result = message.content[0].text.strip()
        if result in ("BOILERPLATE", "INAPPROPRIATE"):
            return None
        return result
    except Exception as e:
        logging.warning(f"Claude summary generation error: {e}")
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_db.py::test_generate_claude_summary tests/test_db.py::test_generate_claude_summary_boilerplate -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add open_journalism_bot.py tests/test_db.py
git commit -m "feat: add generate_claude_summary for newsletter-length summaries"
```

### Task 6: Generate and store `claude_summary` at discovery time (Phase 1)

**Files:**
- Modify: `open_journalism_bot.py:697-727` (Phase 1 loop)

The existing Phase 3 posting flow is **not modified**. The `claude_summary` is generated in Phase 1 alongside metadata collection, right after `insert_repo`. This keeps the summary generation coupled with discovery (when the README is freshest) and avoids any changes to the posting logic.

- [ ] **Step 1: Add `claude_summary` generation to Phase 1**

In the Phase 1 loop (around lines 719-727), after `insert_repo` and the empty/new logging, add:

```python
            if not empty and config.get('anthropic_api_key'):
                readme_content = fetch_readme(repo['full_name'], token=config['github_token'])
                if readme_content:
                    claude_summary = generate_claude_summary(readme_content, config['anthropic_api_key'])
                    if claude_summary:
                        conn.execute(
                            "UPDATE repos SET claude_summary = ? WHERE full_name = ?",
                            (claude_summary, repo['full_name']),
                        )
                        conn.commit()
                        logging.info(f"{repo['full_name']}: generated claude_summary")
```

Note: We only generate summaries for non-empty repos. Empty repos will get their summary when they're rechecked and found to have content (a future enhancement, not in scope here).

- [ ] **Step 2: Run all tests**

Run: `uv run pytest tests/ -v`
Expected: All pass. Phase 1 changes don't affect existing test paths.

- [ ] **Step 3: Manual smoke test**

Run: `uv run open_journalism_bot.py --org mtfreepress --minutes 1440000 --dry-run --verbose`

Check that `claude_summary` is populated:

```bash
sqlite3 data/oj-bot.db "SELECT full_name, substr(claude_summary, 1, 100) FROM repos WHERE claude_summary IS NOT NULL LIMIT 5;"
```

- [ ] **Step 4: Commit**

```bash
git add open_journalism_bot.py
git commit -m "feat: generate claude_summary at discovery time in Phase 1"
```

## Chunk 3: Update the `/repo-summaries` Skill

### Task 7: Simplify the skill to read from SQLite

**Files:**
- Modify: `~/.claude/skills/repo-summaries/SKILL.md`

Now that metadata and summaries are in the database, the skill no longer needs to make GitHub API calls or WebFetch requests. It becomes a simple query-and-format operation.

- [ ] **Step 1: Update the skill**

Replace the Steps section to:

1. Query the database for posted repos and abandoned repos in the date range (same SQL as before)
2. For each repo, read `claude_summary`, `earliest_commit_date`, `homepage_url`, `committer_name`, `committer_bio` from the row
3. If `claude_summary` is NULL (older repo inserted before this feature), fall back to the current behavior: fetch README via WebFetch and summarize
4. Format and save to `summaries/<start>-to-<end>.md`

The output format stays the same. The boilerplate README detection becomes a non-issue for new repos since Claude handles it at generation time. The fallback path preserves backward compatibility for repos already in the DB.

- [ ] **Step 2: Test the skill**

Run: `/repo-summaries march 1-10`

Verify it reads from the database columns instead of making API calls. For repos with `claude_summary` populated, the output should come directly from the DB. For repos without it, it should fall back to WebFetch.

- [ ] **Step 3: Commit**

```bash
git add ~/.claude/skills/repo-summaries/SKILL.md
git commit -m "feat: repo-summaries skill reads from SQLite instead of GitHub API"
```

## Notes

- **No changes to posting behavior**: Phase 3 (the posting loop) is completely untouched. The existing `summary`, `get_repo_descriptions`, and `post_to_bluesky` flow remains exactly as-is.
- **Rate limiting**: `fetch_repo_metadata` adds 2-3 GitHub API calls per new repo (commits, last page of commits, user profile). `generate_claude_summary` adds 1 Anthropic API call. With the bot discovering 0-3 new repos per run, this adds at most ~12 extra API calls per run, well within limits.
- **Dry-run behavior**: Both metadata and `claude_summary` are collected in Phase 1 (discovery), which writes to the DB regardless of `--dry-run`. The `--dry-run` flag only suppresses BlueSky posting (Phase 3). This is correct: we want to stow the data even during test runs.
- **Backfill**: Existing repos in the DB won't have metadata or `claude_summary`. The skill's fallback path (fetch via WebFetch) handles this. A one-time backfill script could be written later if needed.
