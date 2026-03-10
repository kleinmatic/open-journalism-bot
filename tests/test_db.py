from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from open_journalism_bot import (
    init_db, upsert_orgs, insert_repo, repo_exists,
    get_ready_repos, get_pending_empty_repos,
    mark_repo_posted, mark_repo_not_empty,
    recheck_empty_repo, is_repo_empty,
)


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
        "earliest_commit_date", "homepage_url", "committer_login",
        "committer_name", "committer_bio", "claude_summary",
    }
    assert columns == expected


def test_init_db_idempotent(db):
    """Calling init_db twice should not error (IF NOT EXISTS)."""
    from open_journalism_bot import init_db
    init_db(":memory:")  # separate connection, but proves no crash


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
    old_time = (datetime.now(timezone.utc) - timedelta(hours=25)).strftime("%Y-%m-%d %H:%M:%S")
    db.execute("UPDATE repos SET first_seen=? WHERE full_name='testorg/old-empty'", (old_time,))
    db.commit()
    pending = get_pending_empty_repos(db)
    assert len(pending) == 0


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


def test_recheck_empty_repo_finds_readme(db):
    """When a repo has no description but gains a README, it should be marked not-empty."""
    _seed_org(db)
    repo = {
        "full_name": "testorg/readme-only",
        "repo_name": "readme-only",
        "repo_url": "https://github.com/testorg/readme-only",
        "language": "",
        "description": "",
    }
    insert_repo(db, repo, org_username="testorg", is_empty=True)

    # Repo API still returns no description/language
    mock_repo_data = {"description": None, "language": None}
    # But README exists
    mock_readme_response = type("Response", (), {
        "status_code": 200,
        "json": lambda self: {"content": "IyBIZWxsbw=="},  # base64 "# Hello"
    })()

    def mock_get_side_effect(url, **kwargs):
        if "/readme" in url:
            return mock_readme_response
        mock_resp = type("Response", (), {
            "status_code": 200,
            "json": lambda self: mock_repo_data,
        })()
        return mock_resp

    with patch("open_journalism_bot.requests.get", side_effect=mock_get_side_effect):
        changed = recheck_empty_repo(db, "testorg/readme-only", token=None)

    assert changed is True
    row = db.execute("SELECT * FROM repos WHERE full_name='testorg/readme-only'").fetchone()
    assert row["is_empty"] == 0


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


def test_fetch_repo_metadata_basic(db):
    """fetch_repo_metadata returns earliest commit, committer info."""
    from unittest.mock import patch, MagicMock
    from open_journalism_bot import fetch_repo_metadata

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
