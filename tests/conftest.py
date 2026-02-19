import sqlite3
import pytest


@pytest.fixture
def db():
    """In-memory SQLite database for testing."""
    from open_journalism_bot import init_db
    conn = init_db(":memory:")
    yield conn
    conn.close()
