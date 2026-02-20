"""Tests for secnews.utils_search — search caching, feed processing, pruning."""

import json
import os
from unittest.mock import patch, MagicMock

import pytest

from secnews.utils_search import (
    execute_searches,
    process_feed,
    assemble_feeds,
    prune_feeds,
    _load_search_state,
    _save_search_state,
)
from tests.conftest import SAMPLE_ARXIV_FEED


# ---------------------------------------------------------------------------
# Search state persistence
# ---------------------------------------------------------------------------


class TestSearchState:

    def test_save_and_load(self, tmp_path):
        path = str(tmp_path / "state.json")
        state = {"query_a": "2026-02-20T10:00:00Z"}
        _save_search_state(state, path)
        loaded = _load_search_state(path)
        assert loaded == state

    def test_load_missing_file(self, tmp_path):
        assert _load_search_state(str(tmp_path / "nope.json")) == {}


# ---------------------------------------------------------------------------
# Search cache logic
# ---------------------------------------------------------------------------


class TestSearchCaching:

    def _make_params(self, query):
        return [{"search_query": query, "start": 0, "max_results": 10}]

    @patch("secnews.utils_search.requests.get")
    def test_fresh_cache_skips_search(self, mock_get, tmp_path):
        """A recently completed search should be skipped (no HTTP call)."""
        state_path = str(tmp_path / "state.json")
        # Pre-populate with a very recent timestamp
        _save_search_state(
            {"test_query": "2099-01-01T00:00:00Z"}, state_path
        )
        results = execute_searches(
            base="http://fake",
            params=self._make_params("test_query"),
            state_path=state_path,
            cache_hours=1,
        )
        mock_get.assert_not_called()
        assert results == []

    @patch("secnews.utils_search.requests.get")
    def test_stale_cache_executes_search(self, mock_get, tmp_path):
        """A stale cache entry should trigger a real search."""
        state_path = str(tmp_path / "state.json")
        _save_search_state(
            {"test_query": "2020-01-01T00:00:00Z"}, state_path
        )
        mock_response = MagicMock()
        mock_response.content = SAMPLE_ARXIV_FEED
        mock_get.return_value = mock_response

        results = execute_searches(
            base="http://fake",
            params=self._make_params("test_query"),
            state_path=state_path,
            cache_hours=1,
        )
        mock_get.assert_called_once()
        assert len(results) == 1  # one feed batch

    @patch("secnews.utils_search.requests.get")
    def test_force_ignores_fresh_cache(self, mock_get, tmp_path):
        """force=True should execute even if cache is fresh."""
        state_path = str(tmp_path / "state.json")
        _save_search_state(
            {"test_query": "2099-01-01T00:00:00Z"}, state_path
        )
        mock_response = MagicMock()
        mock_response.content = SAMPLE_ARXIV_FEED
        mock_get.return_value = mock_response

        results = execute_searches(
            base="http://fake",
            params=self._make_params("test_query"),
            state_path=state_path,
            cache_hours=1,
            force=True,
        )
        mock_get.assert_called_once()
        assert len(results) == 1

    @patch("secnews.utils_search.requests.get")
    def test_state_saved_per_query_for_resumability(self, mock_get, tmp_path):
        """State is saved after each successful query. If a later query fails,
        earlier queries are still recorded in the state file."""
        state_path = str(tmp_path / "state.json")

        ok_response = MagicMock()
        ok_response.content = SAMPLE_ARXIV_FEED

        # First call succeeds, second raises
        mock_get.side_effect = [ok_response, ConnectionError("arXiv down")]

        params = [
            {"search_query": "query_1", "start": 0, "max_results": 10},
            {"search_query": "query_2", "start": 0, "max_results": 10},
        ]

        with pytest.raises(ConnectionError):
            execute_searches(
                base="http://fake",
                params=params,
                state_path=state_path,
            )

        state = _load_search_state(state_path)
        assert "query_1" in state  # completed before the crash
        assert "query_2" not in state  # never completed


# ---------------------------------------------------------------------------
# Feed processing
# ---------------------------------------------------------------------------


class TestProcessFeed:

    def test_parses_entries_and_inserts(self, tmp_db):
        results = process_feed(SAMPLE_ARXIV_FEED, tmp_db)
        assert len(results) == 2

        # Check first entry
        assert results[0]["id"] == "2601.00001v1"
        assert results[0]["url"] == "http://arxiv.org/pdf/2601.00001v1.pdf"
        assert results[0]["title"] == "Test Paper Alpha: LLM Jailbreak"
        assert results[0]["authors"] == ["Alice Smith", "Bob Jones"]
        assert results[0]["downloaded"] is False
        assert results[0]["summarized"] is False

        # Second entry has one author
        assert results[1]["authors"] == ["Charlie Brown"]

        # Both inserted into DB
        assert tmp_db.has_url("http://arxiv.org/pdf/2601.00001v1.pdf")
        assert tmp_db.has_url("http://arxiv.org/pdf/2601.00002v1.pdf")

    def test_no_duplicate_insert(self, tmp_db):
        """Second call with the same feed should not duplicate records."""
        process_feed(SAMPLE_ARXIV_FEED, tmp_db)
        process_feed(SAMPLE_ARXIV_FEED, tmp_db)
        assert len(tmp_db.find()) == 2

    def test_url_construction_no_double_pdf(self, tmp_db):
        """URL should end in .pdf exactly once."""
        results = process_feed(SAMPLE_ARXIV_FEED, tmp_db)
        for r in results:
            assert r["url"].endswith(".pdf")
            assert not r["url"].endswith(".pdf.pdf")


class TestAssembleFeeds:

    def test_deduplicates_across_feeds(self, tmp_db):
        """Same entries appearing in multiple feeds are deduplicated."""
        results = assemble_feeds(
            feeds=[SAMPLE_ARXIV_FEED, SAMPLE_ARXIV_FEED], paper_db=tmp_db
        )
        urls = [r["url"] for r in results]
        assert len(urls) == len(set(urls))
        assert len(results) == 2  # 2 unique entries


# ---------------------------------------------------------------------------
# Pruning
# ---------------------------------------------------------------------------


class TestPruneFeeds:

    def test_filters_old_papers(self):
        feeds = [
            {"id": "old", "published": "2020-01-01T00:00:00Z"},
            {"id": "new", "published": "2026-02-15T00:00:00Z"},
        ]
        valid = prune_feeds(
            feeds=feeds,
            pull_window="2026-02-01T00:00:00Z",
            paper_path="/nonexistent",
        )
        assert len(valid) == 1
        assert valid[0]["id"] == "new"

    def test_filters_already_downloaded(self, tmp_path):
        """Papers whose PDF already exists on disk are pruned."""
        paper_path = str(tmp_path)
        # Create a fake PDF file
        (tmp_path / "existing.pdf").write_bytes(b"%PDF")

        feeds = [
            {"id": "existing", "published": "2026-02-15T00:00:00Z"},
            {"id": "missing", "published": "2026-02-15T00:00:00Z"},
        ]
        valid = prune_feeds(
            feeds=feeds, pull_window="2026-02-01T00:00:00Z", paper_path=paper_path
        )
        assert len(valid) == 1
        assert valid[0]["id"] == "missing"

    def test_boundary_date_included(self):
        """A paper published exactly at the pull_window should be included."""
        feeds = [{"id": "boundary", "published": "2026-02-01T00:00:00Z"}]
        valid = prune_feeds(
            feeds=feeds,
            pull_window="2026-02-01T00:00:00Z",
            paper_path="/nonexistent",
        )
        assert len(valid) == 1
