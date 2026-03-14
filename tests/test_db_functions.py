"""
Unit tests for database utility functions (last_modified, children_count).
"""
import sqlite3
import time
from pathlib import Path

import pytest

from app import db


@pytest.mark.unit
class TestLastModified:
    """Tests for last_modified database function."""

    @pytest.fixture
    def fresh_db(self, test_db):
        """Fresh database for each test."""
        conn = sqlite3.connect(test_db)
        db._ensure_schema(conn)
        yield conn
        conn.close()

    def test_last_modified_empty_database(self, fresh_db):
        """last_modified returns current time for empty database."""
        result = db.last_modified(fresh_db)
        assert isinstance(result, float)
        assert result > 0
        # Should be close to current time
        assert abs(result - time.time()) < 5

    def test_last_modified_with_items(self, fresh_db):
        """last_modified returns max mtime from items."""
        mtime = 1609459200.0  # 2021-01-01
        db.upsert_file(fresh_db, "test.cbz", "test.cbz", 1000, mtime, "", ".cbz")

        result = db.last_modified(fresh_db)
        assert result == mtime

    def test_last_modified_returns_max_mtime(self, fresh_db):
        """last_modified returns maximum mtime when multiple items exist."""
        db.upsert_file(fresh_db, "file1.cbz", "file1.cbz", 1000, 1000.0, "", ".cbz")
        db.upsert_file(fresh_db, "file2.cbz", "file2.cbz", 1000, 2000.0, "", ".cbz")
        db.upsert_file(fresh_db, "file3.cbz", "file3.cbz", 1000, 1500.0, "", ".cbz")

        result = db.last_modified(fresh_db)
        assert result == 2000.0


@pytest.mark.unit
class TestChildrenCount:
    """Tests for children_count database function."""

    @pytest.fixture
    def db_with_items(self, test_db):
        """Database with test items."""
        conn = sqlite3.connect(test_db)
        db._ensure_schema(conn)

        # Create directory structure
        db.upsert_dir(conn, "Series", "Series", "", 1000.0)
        db.upsert_dir(conn, "Series/Marvel", "Marvel", "Series", 1000.0)
        db.upsert_file(conn, "Series/Marvel/Comic1.cbz", "Comic1.cbz", 1000, 1000.0, "Series/Marvel", ".cbz")
        db.upsert_file(conn, "Series/Marvel/Comic2.cbz", "Comic2.cbz", 1000, 1000.0, "Series/Marvel", ".cbz")

        conn.commit()
        yield conn
        conn.close()

    def test_children_count_root(self, db_with_items):
        """children_count returns correct count for root."""
        result = db.children_count(db_with_items, "")
        assert result == 1  # Series directory

    def test_children_count_subdirectory(self, db_with_items):
        """children_count returns correct count for subdirectory."""
        result = db.children_count(db_with_items, "Series/Marvel")
        assert result == 2  # Two CBZ files

    def test_children_count_nonexistent(self, db_with_items):
        """children_count returns 0 for non-existent path."""
        result = db.children_count(db_with_items, "NonExistent")
        assert result == 0
