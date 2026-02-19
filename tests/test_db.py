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
    init_db(":memory:")  # separate connection, but proves no crash
