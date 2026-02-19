# Empty Repo Handling Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add SQLite persistence and empty-repo hold-back logic so the bot never posts "I think this is an empty repo" — instead it rechecks hourly for 24h and posts once content appears, or silently abandons.

**Architecture:** Add a `database` module (`open_journalism_bot/db.py` — or keep it in the single file to match current architecture). Since the project is a single `open_journalism_bot.py` file, we'll add database functions directly into that file and add a `tests/` directory for pytest. The main loop changes from "fetch → post immediately" to "fetch → insert to DB → recheck empties → post ready repos."

**Tech Stack:** Python 3.13, SQLite3 (stdlib), pytest (dev dependency), existing dependencies unchanged.

---

### Task 1: Project scaffolding — pytest, .gitignore, data dir

**Files:**
- Modify: `pyproject.toml`
- Modify: `.gitignore`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `data/.gitkeep`

**Step 1: Add pytest as dev dependency and data/ to .gitignore**

In `pyproject.toml`, add a dev dependency group:

```toml
[dependency-groups]
dev = [
    "pytest>=8.0",
]
```

In `.gitignore`, add at the end:

```
# Data (SQLite databases)
data/*.db
```

**Step 2: Create test scaffolding**

Create `tests/__init__.py` (empty file).

Create `tests/conftest.py`:

```python
import sqlite3
import pytest


@pytest.fixture
def db():
    """In-memory SQLite database for testing."""
    from open_journalism_bot import init_db
    conn = init_db(":memory:")
    yield conn
    conn.close()
```

**Step 3: Create data directory**

```bash
mkdir -p data
touch data/.gitkeep
```

**Step 4: Install dev dependencies**

Run: `uv sync`

**Step 5: Verify pytest runs (no tests yet)**

Run: `uv run pytest -v`
Expected: "no tests ran" with exit code 5 (which is fine)

**Step 6: Commit**

```bash
git add pyproject.toml .gitignore tests/ data/.gitkeep
git commit -m "scaffold: add pytest, test dir, data dir for SQLite"
```

---

### Task 2: Database initialization and schema

**Files:**
- Modify: `open_journalism_bot.py` (add `init_db` function)
- Create: `tests/test_db.py`

**Step 1: Write the failing test**

Create `tests/test_db.py`:

```python
from open_journalism_bot import init_db


def test_init_db_creates_tables(db):
    """init_db should create orgs and repos tables."""
    cursor = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = [row[0] for row in cursor.fetchall()]
    assert "orgs" in tables
    assert "repos" in tables


def test_init_db_orgs_schema(db):
    """orgs table should have expected columns."""
    cursor = db.execute("PRAGMA table_info(orgs)")
    columns = {row[1] for row in cursor.fetchall()}
    assert columns == {"github_username", "org_name", "github_url"}


def test_init_db_repos_schema(db):
    """repos table should have expected columns."""
    cursor = db.execute("PRAGMA table_info(repos)")
    columns = {row[1] for row in cursor.fetchall()}
    expected = {
        "full_name", "org", "repo_name", "repo_url", "language",
        "description", "summary", "is_empty", "created_at",
        "first_seen", "bluesky_post_url", "bluesky_post_date",
    }
    assert columns == expected


def test_init_db_idempotent(db):
    """Calling init_db twice should not error (IF NOT EXISTS)."""
    from open_journalism_bot import init_db
    # init_db was already called by the fixture; call it again on same connection
    # We need to test with the same db path, so just run the SQL again
    init_db(":memory:")  # separate connection, but proves no crash
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_db.py -v`
Expected: ImportError — `init_db` doesn't exist yet.

**Step 3: Write minimal implementation**

Add to `open_journalism_bot.py`, after the imports and before `load_config()`:

```python
import sqlite3


def init_db(db_path):
    """Initialize SQLite database and return connection."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS orgs (
            github_username TEXT PRIMARY KEY,
            org_name        TEXT NOT NULL,
            github_url      TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS repos (
            full_name        TEXT PRIMARY KEY,
            org              TEXT NOT NULL REFERENCES orgs(github_username),
            repo_name        TEXT NOT NULL,
            repo_url         TEXT NOT NULL,
            language         TEXT,
            description      TEXT,
            summary          TEXT,
            is_empty         BOOLEAN NOT NULL DEFAULT 0,
            created_at       TIMESTAMP,
            first_seen       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            bluesky_post_url TEXT,
            bluesky_post_date TIMESTAMP
        );
    """)
    conn.commit()
    return conn
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_db.py -v`
Expected: All 4 tests PASS.

**Step 5: Commit**

```bash
git add open_journalism_bot.py tests/test_db.py
git commit -m "feat: add init_db with orgs and repos schema"
```

---

### Task 3: Org upsert function

**Files:**
- Modify: `open_journalism_bot.py` (add `upsert_orgs`)
- Modify: `tests/test_db.py`

**Step 1: Write the failing test**

Add to `tests/test_db.py`:

```python
from open_journalism_bot import init_db, upsert_orgs


def test_upsert_orgs_inserts(db):
    """upsert_orgs should insert new orgs."""
    orgs = [
        {"org_name": "New York Times", "github_url": "https://github.com/nytimes"},
        {"org_name": "ProPublica", "github_url": "https://github.com/propublica"},
    ]
    upsert_orgs(db, orgs)
    rows = db.execute("SELECT * FROM orgs ORDER BY github_username").fetchall()
    assert len(rows) == 2
    assert rows[0]["github_username"] == "nytimes"
    assert rows[0]["org_name"] == "New York Times"


def test_upsert_orgs_updates_name(db):
    """upsert_orgs should update org_name if it changes."""
    orgs = [{"org_name": "NYT", "github_url": "https://github.com/nytimes"}]
    upsert_orgs(db, orgs)
    orgs = [{"org_name": "New York Times", "github_url": "https://github.com/nytimes"}]
    upsert_orgs(db, orgs)
    row = db.execute("SELECT * FROM orgs WHERE github_username='nytimes'").fetchone()
    assert row["org_name"] == "New York Times"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_db.py::test_upsert_orgs_inserts -v`
Expected: ImportError — `upsert_orgs` doesn't exist.

**Step 3: Write minimal implementation**

Add to `open_journalism_bot.py` after `init_db`:

```python
def upsert_orgs(conn, orgs):
    """Insert or update orgs from the CSV org list."""
    for org in orgs:
        username = extract_github_username(org["github_url"])
        conn.execute(
            """INSERT INTO orgs (github_username, org_name, github_url)
               VALUES (?, ?, ?)
               ON CONFLICT(github_username) DO UPDATE SET
                   org_name = excluded.org_name,
                   github_url = excluded.github_url""",
            (username, org["org_name"], org["github_url"]),
        )
    conn.commit()
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_db.py -v -k upsert`
Expected: Both upsert tests PASS.

**Step 5: Commit**

```bash
git add open_journalism_bot.py tests/test_db.py
git commit -m "feat: add upsert_orgs for syncing CSV orgs to database"
```

---

### Task 4: Repo insert and query functions

**Files:**
- Modify: `open_journalism_bot.py` (add `insert_repo`, `repo_exists`, `get_ready_repos`, `get_pending_empty_repos`)
- Modify: `tests/test_db.py`

**Step 1: Write failing tests**

Add to `tests/test_db.py`:

```python
from datetime import datetime, timezone, timedelta
from open_journalism_bot import (
    init_db, upsert_orgs, insert_repo, repo_exists,
    get_ready_repos, get_pending_empty_repos,
)


def _seed_org(db):
    """Helper: insert a test org."""
    upsert_orgs(db, [{"org_name": "Test Org", "github_url": "https://github.com/testorg"}])


def test_insert_repo_and_exists(db):
    _seed_org(db)
    repo = {
        "full_name": "testorg/myrepo",
        "repo_name": "myrepo",
        "repo_url": "https://github.com/testorg/myrepo",
        "language": "Python",
        "description": "A test repo",
    }
    assert not repo_exists(db, "testorg/myrepo")
    insert_repo(db, repo, org_username="testorg", is_empty=False)
    assert repo_exists(db, "testorg/myrepo")


def test_insert_repo_empty(db):
    _seed_org(db)
    repo = {
        "full_name": "testorg/empty",
        "repo_name": "empty",
        "repo_url": "https://github.com/testorg/empty",
        "language": "",
        "description": "",
    }
    insert_repo(db, repo, org_username="testorg", is_empty=True)
    row = db.execute("SELECT is_empty FROM repos WHERE full_name='testorg/empty'").fetchone()
    assert row["is_empty"] == 1


def test_get_ready_repos(db):
    _seed_org(db)
    # Non-empty, not yet posted = ready
    repo = {
        "full_name": "testorg/ready",
        "repo_name": "ready",
        "repo_url": "https://github.com/testorg/ready",
        "language": "Python",
        "description": "Ready to post",
    }
    insert_repo(db, repo, org_username="testorg", is_empty=False)
    ready = get_ready_repos(db)
    assert len(ready) == 1
    assert ready[0]["full_name"] == "testorg/ready"


def test_get_ready_repos_excludes_posted(db):
    _seed_org(db)
    repo = {
        "full_name": "testorg/posted",
        "repo_name": "posted",
        "repo_url": "https://github.com/testorg/posted",
        "language": "Python",
        "description": "Already posted",
    }
    insert_repo(db, repo, org_username="testorg", is_empty=False)
    db.execute(
        "UPDATE repos SET bluesky_post_url='https://bsky.app/post/123' WHERE full_name='testorg/posted'"
    )
    db.commit()
    ready = get_ready_repos(db)
    assert len(ready) == 0


def test_get_ready_repos_excludes_empty(db):
    _seed_org(db)
    repo = {
        "full_name": "testorg/empty",
        "repo_name": "empty",
        "repo_url": "https://github.com/testorg/empty",
        "language": "",
        "description": "",
    }
    insert_repo(db, repo, org_username="testorg", is_empty=True)
    ready = get_ready_repos(db)
    assert len(ready) == 0


def test_get_pending_empty_repos(db):
    _seed_org(db)
    repo = {
        "full_name": "testorg/pending",
        "repo_name": "pending",
        "repo_url": "https://github.com/testorg/pending",
        "language": "",
        "description": "",
    }
    insert_repo(db, repo, org_username="testorg", is_empty=True)
    pending = get_pending_empty_repos(db)
    assert len(pending) == 1
    assert pending[0]["full_name"] == "testorg/pending"


def test_get_pending_empty_repos_excludes_old(db):
    """Repos first_seen > 24h ago should not appear as pending."""
    _seed_org(db)
    repo = {
        "full_name": "testorg/old-empty",
        "repo_name": "old-empty",
        "repo_url": "https://github.com/testorg/old-empty",
        "language": "",
        "description": "",
    }
    insert_repo(db, repo, org_username="testorg", is_empty=True)
    # Backdate first_seen to 25 hours ago
    old_time = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    db.execute("UPDATE repos SET first_seen=? WHERE full_name='testorg/old-empty'", (old_time,))
    db.commit()
    pending = get_pending_empty_repos(db)
    assert len(pending) == 0
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_db.py -v -k "insert or ready or pending"`
Expected: ImportError — functions don't exist yet.

**Step 3: Write minimal implementation**

Add to `open_journalism_bot.py` after `upsert_orgs`:

```python
def insert_repo(conn, repo, org_username, is_empty=False):
    """Insert a new repo into the database."""
    conn.execute(
        """INSERT OR IGNORE INTO repos
           (full_name, org, repo_name, repo_url, language, description, is_empty, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            repo["full_name"],
            org_username,
            repo["repo_name"],
            repo["repo_url"],
            repo.get("language") or None,
            repo.get("description") or None,
            is_empty,
            repo.get("created_at"),
        ),
    )
    conn.commit()


def repo_exists(conn, full_name):
    """Check if a repo is already in the database."""
    row = conn.execute(
        "SELECT 1 FROM repos WHERE full_name = ?", (full_name,)
    ).fetchone()
    return row is not None


def get_ready_repos(conn):
    """Get repos that are ready to post (not empty, not yet posted)."""
    return conn.execute(
        """SELECT r.*, o.org_name FROM repos r
           JOIN orgs o ON r.org = o.github_username
           WHERE r.is_empty = 0 AND r.bluesky_post_url IS NULL"""
    ).fetchall()


def get_pending_empty_repos(conn):
    """Get empty repos still within the 24h recheck window."""
    return conn.execute(
        """SELECT r.*, o.org_name FROM repos r
           JOIN orgs o ON r.org = o.github_username
           WHERE r.is_empty = 1
             AND r.bluesky_post_url IS NULL
             AND r.first_seen > datetime('now', '-24 hours')"""
    ).fetchall()
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_db.py -v`
Expected: All tests PASS.

**Step 5: Commit**

```bash
git add open_journalism_bot.py tests/test_db.py
git commit -m "feat: add repo insert/exists/query functions"
```

---

### Task 5: Mark-posted and update-empty-repo functions

**Files:**
- Modify: `open_journalism_bot.py` (add `mark_repo_posted`, `mark_repo_not_empty`)
- Modify: `tests/test_db.py`

**Step 1: Write failing tests**

Add to `tests/test_db.py`:

```python
from open_journalism_bot import mark_repo_posted, mark_repo_not_empty


def test_mark_repo_posted(db):
    _seed_org(db)
    repo = {
        "full_name": "testorg/topost",
        "repo_name": "topost",
        "repo_url": "https://github.com/testorg/topost",
        "language": "Python",
        "description": "Will be posted",
    }
    insert_repo(db, repo, org_username="testorg", is_empty=False)
    mark_repo_posted(db, "testorg/topost", "https://bsky.app/post/abc123")
    row = db.execute("SELECT * FROM repos WHERE full_name='testorg/topost'").fetchone()
    assert row["bluesky_post_url"] == "https://bsky.app/post/abc123"
    assert row["bluesky_post_date"] is not None


def test_mark_repo_not_empty(db):
    _seed_org(db)
    repo = {
        "full_name": "testorg/was-empty",
        "repo_name": "was-empty",
        "repo_url": "https://github.com/testorg/was-empty",
        "language": "",
        "description": "",
    }
    insert_repo(db, repo, org_username="testorg", is_empty=True)
    mark_repo_not_empty(db, "testorg/was-empty", description="Now has content", language="Python")
    row = db.execute("SELECT * FROM repos WHERE full_name='testorg/was-empty'").fetchone()
    assert row["is_empty"] == 0
    assert row["description"] == "Now has content"
    assert row["language"] == "Python"
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_db.py -v -k "mark_"`
Expected: ImportError.

**Step 3: Write minimal implementation**

Add to `open_journalism_bot.py` after `get_pending_empty_repos`:

```python
def mark_repo_posted(conn, full_name, post_url):
    """Record that a repo has been posted to BlueSky."""
    conn.execute(
        """UPDATE repos SET bluesky_post_url = ?, bluesky_post_date = datetime('now')
           WHERE full_name = ?""",
        (post_url, full_name),
    )
    conn.commit()


def mark_repo_not_empty(conn, full_name, description=None, language=None, summary=None):
    """Update a previously-empty repo with new content."""
    conn.execute(
        """UPDATE repos SET is_empty = 0, description = ?, language = ?, summary = ?
           WHERE full_name = ?""",
        (description, language, summary, full_name),
    )
    conn.commit()
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_db.py -v`
Expected: All tests PASS.

**Step 5: Commit**

```bash
git add open_journalism_bot.py tests/test_db.py
git commit -m "feat: add mark_repo_posted and mark_repo_not_empty"
```

---

### Task 6: Add --db CLI flag

**Files:**
- Modify: `open_journalism_bot.py` (update `parse_args`)

**Step 1: Add the --db argument**

In `parse_args()`, add:

```python
parser.add_argument(
    '--db',
    type=str,
    default=None,
    help='Path to SQLite database (default: data/oj-bot.db)'
)
```

**Step 2: Verify it parses**

Run: `uv run python -c "from open_journalism_bot import parse_args; import sys; sys.argv=['bot','--db','test.db','--dry-run']; print(parse_args())"`
Expected: shows `db='test.db'`

**Step 3: Commit**

```bash
git add open_journalism_bot.py
git commit -m "feat: add --db CLI flag for alternate database path"
```

---

### Task 7: Recheck empty repos function

**Files:**
- Modify: `open_journalism_bot.py` (add `recheck_empty_repo`)
- Modify: `tests/test_db.py`

**Step 1: Write failing test**

Add to `tests/test_db.py`:

```python
from unittest.mock import patch
from open_journalism_bot import recheck_empty_repo


def test_recheck_empty_repo_finds_content(db):
    """When a repo gains a description, it should be marked not-empty."""
    _seed_org(db)
    repo = {
        "full_name": "testorg/filling-up",
        "repo_name": "filling-up",
        "repo_url": "https://github.com/testorg/filling-up",
        "language": "",
        "description": "",
    }
    insert_repo(db, repo, org_username="testorg", is_empty=True)

    # Mock the GitHub API to return a repo with content now
    mock_repo_data = {
        "description": "A real project",
        "language": "JavaScript",
    }
    with patch("open_journalism_bot.requests.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = mock_repo_data
        changed = recheck_empty_repo(db, "testorg/filling-up", token=None)

    assert changed is True
    row = db.execute("SELECT * FROM repos WHERE full_name='testorg/filling-up'").fetchone()
    assert row["is_empty"] == 0
    assert row["description"] == "A real project"


def test_recheck_empty_repo_still_empty(db):
    """When a repo is still empty, it stays marked empty."""
    _seed_org(db)
    repo = {
        "full_name": "testorg/still-empty",
        "repo_name": "still-empty",
        "repo_url": "https://github.com/testorg/still-empty",
        "language": "",
        "description": "",
    }
    insert_repo(db, repo, org_username="testorg", is_empty=True)

    mock_repo_data = {"description": None, "language": None}
    with patch("open_journalism_bot.requests.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = mock_repo_data
        changed = recheck_empty_repo(db, "testorg/still-empty", token=None)

    assert changed is False
    row = db.execute("SELECT * FROM repos WHERE full_name='testorg/still-empty'").fetchone()
    assert row["is_empty"] == 1


def test_recheck_empty_repo_404(db):
    """Deleted repos should be treated as abandoned (no crash)."""
    _seed_org(db)
    repo = {
        "full_name": "testorg/deleted",
        "repo_name": "deleted",
        "repo_url": "https://github.com/testorg/deleted",
        "language": "",
        "description": "",
    }
    insert_repo(db, repo, org_username="testorg", is_empty=True)

    with patch("open_journalism_bot.requests.get") as mock_get:
        mock_get.return_value.status_code = 404
        changed = recheck_empty_repo(db, "testorg/deleted", token=None)

    assert changed is False
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_db.py -v -k recheck`
Expected: ImportError.

**Step 3: Write minimal implementation**

Add to `open_journalism_bot.py`:

```python
def recheck_empty_repo(conn, full_name, token=None):
    """
    Re-fetch a repo from GitHub to see if it has content now.
    Returns True if the repo was updated (no longer empty), False otherwise.
    """
    headers = get_github_headers(token)
    url = f"https://api.github.com/repos/{full_name}"

    try:
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 404:
            logging.info(f"{full_name}: repo deleted, treating as abandoned")
            return False
        if response.status_code != 200:
            logging.warning(f"{full_name}: recheck got status {response.status_code}")
            return False

        data = response.json()
        description = data.get("description") or ""
        language = data.get("language") or ""

        if description or language:
            logging.info(f"{full_name}: repo now has content (desc={bool(description)}, lang={language})")
            mark_repo_not_empty(conn, full_name, description=description or None, language=language or None)
            return True

        logging.info(f"{full_name}: still empty on recheck")
        return False

    except requests.exceptions.RequestException as e:
        logging.warning(f"{full_name}: recheck failed: {e}")
        return False
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_db.py -v -k recheck`
Expected: All 3 recheck tests PASS.

**Step 5: Commit**

```bash
git add open_journalism_bot.py tests/test_db.py
git commit -m "feat: add recheck_empty_repo to detect content in held-back repos"
```

---

### Task 8: Determine if a repo is empty (helper)

**Files:**
- Modify: `open_journalism_bot.py` (add `is_repo_empty`)
- Modify: `tests/test_db.py`

**Step 1: Write failing tests**

Add to `tests/test_db.py`:

```python
from open_journalism_bot import is_repo_empty


def test_is_repo_empty_true():
    """No description, no language = empty."""
    repo = {"description": "", "language": "", "full_name": "org/repo"}
    assert is_repo_empty(repo) is True


def test_is_repo_empty_false_description():
    repo = {"description": "A real project", "language": "", "full_name": "org/repo"}
    assert is_repo_empty(repo) is False


def test_is_repo_empty_false_language():
    repo = {"description": "", "language": "Python", "full_name": "org/repo"}
    assert is_repo_empty(repo) is False
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_db.py -v -k is_repo_empty`
Expected: ImportError.

**Step 3: Write minimal implementation**

Add to `open_journalism_bot.py`:

```python
def is_repo_empty(repo):
    """Determine if a repo appears to be empty (no description, no language)."""
    return not repo.get("description") and not repo.get("language")
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_db.py -v -k is_repo_empty`
Expected: All 3 PASS.

**Step 5: Commit**

```bash
git add open_journalism_bot.py tests/test_db.py
git commit -m "feat: add is_repo_empty helper"
```

---

### Task 9: Integrate database into main() — the big refactor

This is the largest task. The current `main()` does: fetch CSV → for each org fetch repos → for each repo post immediately. The new flow is:

1. Open/init database
2. Sync orgs from CSV
3. Fetch new repos → insert to DB (skip if already known)
4. Recheck pending empty repos
5. Get descriptions for ready repos
6. Post ready repos (or dry-run log them)
7. Log stats

**Files:**
- Modify: `open_journalism_bot.py` (rewrite `main()`)

**Step 1: Rewrite main()**

The key changes to `main()`:

```python
def main():
    args = parse_args()
    setup_logging(verbose=args.verbose)

    try:
        config = load_config()
    except ValueError as e:
        logging.error(f"Configuration error: {e}")
        sys.exit(1)

    if args.minutes is not None:
        config['check_minutes'] = args.minutes
    if args.dry_run:
        config['test_mode'] = True

    # Database setup
    dry_run = config['test_mode']
    db_path = args.db or str(Path(__file__).parent / 'data' / 'oj-bot.db')

    if dry_run:
        # Dry run: use in-memory DB seeded from disk if it exists
        conn = init_db(":memory:")
        disk_db = Path(db_path)
        if disk_db.exists():
            disk_conn = sqlite3.connect(str(disk_db))
            disk_conn.backup(conn)
            disk_conn.close()
            logging.info(f"Dry run: loaded snapshot from {db_path}")
        else:
            logging.info("Dry run: using fresh in-memory database")
    else:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = init_db(db_path)
        logging.info(f"Using database: {db_path}")

    # Fetch and sync orgs
    logging.info(f"Fetching CSV from {config['csv_url']}...")
    try:
        csv_content = fetch_csv(config['csv_url'])
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to fetch CSV: {e}")
        sys.exit(1)

    all_orgs = parse_csv(csv_content)

    # Handle --org filter
    if args.org:
        handle = args.org.lower().rstrip('/')
        if handle.startswith('http'):
            handle = handle.split('/')[-1]
        matching = [o for o in all_orgs if extract_github_username(o['github_url']).lower() == handle]
        if matching:
            orgs = matching
        else:
            github_url = f'https://github.com/{handle}'
            display_name = args.name if args.name else handle
            orgs = [{'org_name': display_name, 'github_url': github_url}]
    else:
        orgs = all_orgs
        if args.limit > 0:
            orgs = orgs[:args.limit]

    if not dry_run:
        upsert_orgs(conn, orgs)

    logging.info(f"Checking {len(orgs)} organizations for repos created in last {config['check_minutes']} minutes...")

    if not config['github_token']:
        logging.warning("No GITHUB_TOKEN set. Rate limited to 60 requests/hour.")
    if not config['anthropic_api_key']:
        logging.warning("No ANTHROPIC_API_KEY set. AI-generated descriptions disabled.")

    template = load_template()

    # Initialize BlueSky client if not in test mode
    bluesky_client = None
    if not dry_run:
        logging.info("Logging into BlueSky...")
        bluesky_client = Client()
        bluesky_client.login(config['bluesky_handle'], config['bluesky_password'])

    # Phase 1: Discover new repos
    new_count = 0
    empty_count = 0
    orgs_checked = 0

    for org in orgs:
        username = extract_github_username(org['github_url'])
        try:
            repos = fetch_recent_repos(
                org['github_url'],
                token=config['github_token'],
                minutes=config['check_minutes'],
            )
            orgs_checked += 1
        except RateLimitError as e:
            logging.error(f"Rate limit hit after {orgs_checked} orgs: {e}")
            break

        for repo in repos:
            if repo_exists(conn, repo['full_name']):
                continue

            empty = is_repo_empty(repo)
            if not dry_run:
                insert_repo(conn, repo, org_username=username, is_empty=empty)

            if empty:
                empty_count += 1
                logging.info(f"{repo['full_name']}: empty, holding back for recheck")
            else:
                new_count += 1
                logging.info(f"{repo['full_name']}: new repo with content")

    # Phase 2: Recheck pending empty repos
    rechecked = 0
    recovered = 0
    if not dry_run:
        pending = get_pending_empty_repos(conn)
        for row in pending:
            changed = recheck_empty_repo(conn, row['full_name'], token=config['github_token'])
            rechecked += 1
            if changed:
                recovered += 1

    # Phase 3: Post ready repos
    posted = 0
    if dry_run:
        # In dry-run mode, process newly-found non-empty repos the old way
        for org in orgs:
            try:
                repos = fetch_recent_repos(
                    org['github_url'],
                    token=config['github_token'],
                    minutes=config['check_minutes'],
                )
            except RateLimitError:
                break
            for repo in repos:
                if is_repo_empty(repo):
                    continue
                descriptions = get_repo_descriptions(
                    repo,
                    token=config['github_token'],
                    anthropic_api_key=config['anthropic_api_key'],
                )
                post_text = render_post(template, org['org_name'], repo, descriptions['imputed_description'])
                logging.info("--- DRY RUN: Would post ---")
                logging.info(post_text)
                logging.info(f"[Link Card] Title: {repo['repo_name']}")
                logging.info(f"[Link Card] Description: {descriptions['github_description'] or '(none)'}")
                logging.info(f"[Link Card] URL: {repo['repo_url']}")
                logging.info("----------------------------")
                posted += 1
    else:
        ready = get_ready_repos(conn)
        for row in ready:
            repo = {
                'full_name': row['full_name'],
                'repo_name': row['repo_name'],
                'repo_url': row['repo_url'],
                'language': row['language'] or '',
                'description': row['description'] or '',
            }
            descriptions = get_repo_descriptions(
                repo,
                token=config['github_token'],
                anthropic_api_key=config['anthropic_api_key'],
            )
            # Store summary if we got one
            if descriptions['imputed_description']:
                conn.execute(
                    "UPDATE repos SET summary = ? WHERE full_name = ?",
                    (descriptions['imputed_description'], row['full_name']),
                )
                conn.commit()

            post_text = render_post(template, row['org_name'], repo, descriptions['imputed_description'])
            logging.info(f"Posting about {row['org_name']}/{row['repo_name']}...")
            post_to_bluesky(bluesky_client, post_text, repo, descriptions['github_description'])
            mark_repo_posted(conn, row['full_name'], "posted")  # TODO: get actual post URL from atproto response
            posted += 1

    # Stats
    logging.info(
        f"Done. Checked {orgs_checked} orgs. "
        f"New: {new_count}, held back (empty): {empty_count}, "
        f"rechecked: {rechecked}, recovered: {recovered}, "
        f"posted: {posted}."
    )
    conn.close()
```

Note: The dry-run path re-fetches repos to avoid writing to the database, keeping dry-run fully side-effect-free. This means dry-run makes double the API calls, which is acceptable for a debugging tool.

**Step 2: Manual smoke test**

Run: `uv run open_journalism_bot.py --org nytimes --minutes 10000000 --dry-run --verbose`
Expected: Same output as before (finds repos, prints what would be posted), but no database file created.

Verify no database was created:
Run: `ls data/` — should only contain `.gitkeep`.

**Step 3: Run all tests**

Run: `uv run pytest -v`
Expected: All tests PASS (the unit tests use in-memory DB and don't touch main()).

**Step 4: Commit**

```bash
git add open_journalism_bot.py
git commit -m "feat: integrate SQLite database into main loop with empty-repo hold-back"
```

---

### Task 10: Get actual post URL from atproto response

**Files:**
- Modify: `open_journalism_bot.py` (update `post_to_bluesky` to return the post URL)

**Step 1: Update post_to_bluesky**

Change `post_to_bluesky` to return the post URI/URL:

```python
def post_to_bluesky(client, text, repo, github_description=''):
    """Post text to BlueSky with a link card. Returns the post URI."""
    embed = create_link_card(repo, github_description)
    response = client.send_post(text=text, embed=embed)
    return response.uri
```

And in `main()`, update the posting section to use the returned URI:

```python
post_uri = post_to_bluesky(bluesky_client, post_text, repo, descriptions['github_description'])
mark_repo_posted(conn, row['full_name'], post_uri)
```

**Step 2: Commit**

```bash
git add open_journalism_bot.py
git commit -m "feat: capture actual post URI from BlueSky response"
```

---

### Task 11: End-to-end integration test

**Files:**
- Create: `tests/test_integration.py`

**Step 1: Write integration test**

```python
"""Integration test for the full bot flow using mocked HTTP."""
from unittest.mock import patch, MagicMock
from open_journalism_bot import (
    init_db, upsert_orgs, insert_repo, is_repo_empty,
    recheck_empty_repo, get_ready_repos, get_pending_empty_repos,
    get_repo_descriptions, mark_repo_posted,
)


def test_full_flow_non_empty_repo():
    """A repo with content should go straight to ready."""
    conn = init_db(":memory:")
    upsert_orgs(conn, [{"org_name": "Test", "github_url": "https://github.com/testorg"}])

    repo = {
        "full_name": "testorg/real-project",
        "repo_name": "real-project",
        "repo_url": "https://github.com/testorg/real-project",
        "language": "Python",
        "description": "A real project",
    }
    assert not is_repo_empty(repo)
    insert_repo(conn, repo, org_username="testorg", is_empty=False)

    ready = get_ready_repos(conn)
    assert len(ready) == 1

    mark_repo_posted(conn, "testorg/real-project", "at://did:plc:xxx/app.bsky.feed.post/yyy")
    ready = get_ready_repos(conn)
    assert len(ready) == 0
    conn.close()


def test_full_flow_empty_repo_recovers():
    """An empty repo should be held back, then posted after recheck finds content."""
    conn = init_db(":memory:")
    upsert_orgs(conn, [{"org_name": "Test", "github_url": "https://github.com/testorg"}])

    repo = {
        "full_name": "testorg/new-empty",
        "repo_name": "new-empty",
        "repo_url": "https://github.com/testorg/new-empty",
        "language": "",
        "description": "",
    }
    assert is_repo_empty(repo)
    insert_repo(conn, repo, org_username="testorg", is_empty=True)

    # Should not be ready yet
    assert len(get_ready_repos(conn)) == 0
    # Should be pending
    assert len(get_pending_empty_repos(conn)) == 1

    # Simulate recheck finding content
    mock_data = {"description": "Now has a description", "language": "Go"}
    with patch("open_journalism_bot.requests.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = mock_data
        recheck_empty_repo(conn, "testorg/new-empty", token=None)

    # Now it should be ready
    assert len(get_ready_repos(conn)) == 1
    assert len(get_pending_empty_repos(conn)) == 0
    conn.close()
```

**Step 2: Run all tests**

Run: `uv run pytest -v`
Expected: All tests PASS.

**Step 3: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: add end-to-end integration tests for empty repo flow"
```

---

### Task 12: Final manual testing and cleanup

**Step 1: Full dry-run test**

Run: `uv run open_journalism_bot.py --limit 5 --dry-run --verbose`
Expected: Bot runs, finds repos, logs what it would post. No `data/oj-bot.db` created.

**Step 2: Test with alternate database**

Run: `uv run open_journalism_bot.py --org nytimes --minutes 1440 --db /tmp/test-bot.db --dry-run --verbose`
Expected: Works fine, no `/tmp/test-bot.db` created (dry-run).

**Step 3: Run full test suite**

Run: `uv run pytest -v`
Expected: All tests PASS.

**Step 4: Final commit (if any cleanup needed)**

```bash
git add -A && git commit -m "chore: cleanup after empty repo handling implementation"
```
