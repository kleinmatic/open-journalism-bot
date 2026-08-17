from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
from open_journalism_bot import (
    init_db, upsert_orgs, insert_repo, repo_exists,
    get_ready_repos, get_pending_empty_repos,
    mark_repo_posted, mark_repo_not_empty,
    recheck_empty_repo, is_repo_empty,
    mark_repo_posted_mastodon, get_mastodon_backlog,
    mastodon_enabled, post_to_mastodon, try_post_to_mastodon,
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
        "license", "backfill_source",
        "ai_signals_json", "ai_signals_checked_at",
        "mastodon_post_url", "mastodon_post_date",
    }
    assert columns == expected


def test_init_db_idempotent(db):
    """Calling init_db twice should not error (IF NOT EXISTS)."""
    from open_journalism_bot import init_db
    init_db(":memory:")  # separate connection, but proves no crash


def test_upsert_orgs_inserts(db):
    """upsert_orgs should insert new orgs and return their usernames."""
    orgs = [
        {"org_name": "New York Times", "github_url": "https://github.com/nytimes"},
        {"org_name": "ProPublica", "github_url": "https://github.com/propublica"},
    ]
    new_orgs = upsert_orgs(db, orgs)
    rows = db.execute("SELECT * FROM orgs ORDER BY github_username").fetchall()
    assert len(rows) == 2
    assert rows[0]["github_username"] == "nytimes"
    assert rows[0]["org_name"] == "New York Times"
    assert new_orgs == {"nytimes", "propublica"}


def test_upsert_orgs_updates_name(db):
    """upsert_orgs should update org_name if it changes, returning no new orgs."""
    orgs = [{"org_name": "NYT", "github_url": "https://github.com/nytimes"}]
    upsert_orgs(db, orgs)
    orgs = [{"org_name": "New York Times", "github_url": "https://github.com/nytimes"}]
    new_orgs = upsert_orgs(db, orgs)
    row = db.execute("SELECT * FROM orgs WHERE github_username='nytimes'").fetchone()
    assert row["org_name"] == "New York Times"
    assert new_orgs == set()


def test_upsert_orgs_mixed_new_and_existing(db):
    """upsert_orgs should only return genuinely new orgs."""
    upsert_orgs(db, [{"org_name": "NYT", "github_url": "https://github.com/nytimes"}])
    new_orgs = upsert_orgs(db, [
        {"org_name": "New York Times", "github_url": "https://github.com/nytimes"},
        {"org_name": "ProPublica", "github_url": "https://github.com/propublica"},
    ])
    assert new_orgs == {"propublica"}


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


def test_get_ready_repos_excludes_seeded(db):
    """Repos seeded when a new org is added should not be ready to post."""
    _seed_org(db)
    repo = {
        "full_name": "testorg/seeded",
        "repo_name": "seeded",
        "repo_url": "https://github.com/testorg/seeded",
        "language": "Python",
        "description": "Existing repo from new org",
    }
    insert_repo(db, repo, org_username="testorg", is_empty=False)
    db.execute(
        "UPDATE repos SET backfill_source = 'org-seed 2026-03-18' WHERE full_name='testorg/seeded'"
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


def test_get_ready_repos_excludes_pudding(db):
    """The Pudding repos are never auto-posted (no-scoop: they open repos
    months before the story publishes)."""
    _seed_org(db)
    upsert_orgs(db, [{"org_name": "The Pudding", "github_url": "https://github.com/the-pudding"}])
    # A non-empty, unposted, organically-discovered Pudding repo — would be
    # "ready" by every other criterion, but must be excluded.
    pudding_repo = {
        "full_name": "the-pudding/secret-story",
        "repo_name": "secret-story",
        "repo_url": "https://github.com/the-pudding/secret-story",
        "language": "Svelte",
        "description": "Svelte starter boilerplate",
    }
    insert_repo(db, pudding_repo, org_username="the-pudding", is_empty=False)
    # A normal repo that should still be ready, to prove the filter is org-specific.
    other_repo = {
        "full_name": "testorg/ready",
        "repo_name": "ready",
        "repo_url": "https://github.com/testorg/ready",
        "language": "Python",
        "description": "Ready to post",
    }
    insert_repo(db, other_repo, org_username="testorg", is_empty=False)
    ready = get_ready_repos(db)
    assert [r["full_name"] for r in ready] == ["testorg/ready"]


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


def test_generate_claude_summary(db):
    """generate_claude_summary returns a 3-5 sentence summary."""
    from unittest.mock import patch, MagicMock
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
    from unittest.mock import patch, MagicMock
    from open_journalism_bot import generate_claude_summary

    mock_message = MagicMock()
    mock_message.content = [MagicMock(text="BOILERPLATE")]

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_message

    with patch("open_journalism_bot.anthropic.Anthropic", return_value=mock_client):
        result = generate_claude_summary("# Getting Started\nRun npm install", "fake-key")

    assert result is None


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


# --- Mastodon cross-posting ---

MASTO_CONFIG = {
    "mastodon_api_url": "https://mastodon.palewi.re",
    "mastodon_token": "fake-token",
}


def _insert_posted_repo(db, full_name="testorg/proj", posted_hours_ago=1):
    """Insert a repo already posted to BlueSky N hours ago."""
    upsert_orgs(db, [{"org_name": "Test", "github_url": "https://github.com/testorg"}])
    insert_repo(db, {
        "full_name": full_name,
        "repo_name": full_name.split("/")[1],
        "repo_url": f"https://github.com/{full_name}",
        "language": "Python",
        "description": "A project",
    }, org_username="testorg", is_empty=False)
    db.execute(
        """UPDATE repos SET bluesky_post_url = 'at://xxx',
               bluesky_post_date = datetime('now', ?) WHERE full_name = ?""",
        (f"-{posted_hours_ago} hours", full_name),
    )
    db.commit()


def test_mastodon_enabled_requires_both_settings():
    """Mastodon stays off unless both the API URL and token are set."""
    assert mastodon_enabled(MASTO_CONFIG)
    assert not mastodon_enabled({"mastodon_api_url": "", "mastodon_token": "t"})
    assert not mastodon_enabled({"mastodon_api_url": "https://x", "mastodon_token": None})
    assert not mastodon_enabled({})


def test_mark_repo_posted_mastodon_sets_url_and_date(db):
    """mark_repo_posted_mastodon records the status URL and a timestamp."""
    _insert_posted_repo(db)
    mark_repo_posted_mastodon(db, "testorg/proj", "https://mastodon.palewi.re/@bot/123")
    row = db.execute("SELECT * FROM repos WHERE full_name = 'testorg/proj'").fetchone()
    assert row["mastodon_post_url"] == "https://mastodon.palewi.re/@bot/123"
    assert row["mastodon_post_date"] is not None
    # BlueSky tracking is untouched
    assert row["bluesky_post_url"] == "at://xxx"


def test_post_to_mastodon_sends_expected_request():
    """post_to_mastodon hits /api/v1/statuses with bearer auth and returns the URL."""
    mock_response = MagicMock()
    mock_response.json.return_value = {"url": "https://mastodon.palewi.re/@bot/999"}

    with patch("open_journalism_bot.requests.post", return_value=mock_response) as mock_post:
        url = post_to_mastodon(MASTO_CONFIG, "Hello world", idempotency_key="testorg/proj")

    assert url == "https://mastodon.palewi.re/@bot/999"
    args, kwargs = mock_post.call_args
    assert args[0] == "https://mastodon.palewi.re/api/v1/statuses"
    assert kwargs["headers"]["Authorization"] == "Bearer fake-token"
    assert kwargs["headers"]["Idempotency-Key"] == "testorg/proj"
    assert kwargs["data"]["status"] == "Hello world"
    assert kwargs["data"]["visibility"] == "public"
    mock_response.raise_for_status.assert_called_once()


def test_try_post_to_mastodon_swallows_failures(db):
    """A Mastodon outage must not raise — BlueSky is the primary channel."""
    _insert_posted_repo(db)
    with patch("open_journalism_bot.post_to_mastodon", side_effect=RuntimeError("503")):
        assert try_post_to_mastodon(db, MASTO_CONFIG, "text", "testorg/proj") is False
    row = db.execute("SELECT * FROM repos WHERE full_name = 'testorg/proj'").fetchone()
    assert row["mastodon_post_url"] is None


def test_mastodon_backlog_picks_up_missed_post(db):
    """A repo posted to BlueSky but not Mastodon shows up in the catch-up sweep."""
    _insert_posted_repo(db, posted_hours_ago=2)
    assert [r["full_name"] for r in get_mastodon_backlog(db)] == ["testorg/proj"]

    mark_repo_posted_mastodon(db, "testorg/proj", "https://mastodon.palewi.re/@bot/1")
    assert get_mastodon_backlog(db) == []


def test_mastodon_backlog_ignores_old_posts(db):
    """The sweep is time-bounded so enabling Mastodon doesn't replay the back catalog."""
    _insert_posted_repo(db, posted_hours_ago=72)
    assert get_mastodon_backlog(db) == []
    assert len(get_mastodon_backlog(db, hours=96)) == 1


def test_mastodon_backlog_ignores_unposted_repos(db):
    """Repos never posted to BlueSky aren't Mastodon backlog — they're still 'ready'."""
    upsert_orgs(db, [{"org_name": "Test", "github_url": "https://github.com/testorg"}])
    insert_repo(db, {
        "full_name": "testorg/fresh",
        "repo_name": "fresh",
        "repo_url": "https://github.com/testorg/fresh",
        "language": "Go",
        "description": "New",
    }, org_username="testorg", is_empty=False)
    assert get_mastodon_backlog(db) == []
    assert len(get_ready_repos(db)) == 1
