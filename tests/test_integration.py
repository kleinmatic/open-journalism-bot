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
