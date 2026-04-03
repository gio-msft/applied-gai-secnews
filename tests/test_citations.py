"""Tests for secnews.utils_citations and build_viz author-overlap logic."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from secnews.utils_citations import (
    _extract_arxiv_ids,
    build_citation_edges,
    fetch_citations,
    load_cache,
    save_cache,
)
from build_viz import _normalize_author, build_author_edges


# ---------------------------------------------------------------------------
# _extract_arxiv_ids
# ---------------------------------------------------------------------------


class TestExtractArxivIds:

    def test_extracts_arxiv_ids(self):
        refs = [
            {"externalIds": {"ArXiv": "2301.00001", "DOI": "10.1234/foo"}},
            {"externalIds": {"ArXiv": "2302.00002"}},
            {"externalIds": {"DOI": "10.5678/bar"}},  # no ArXiv
            None,  # paper not found
        ]
        result = _extract_arxiv_ids(refs)
        assert result == {"2301.00001", "2302.00002"}

    def test_empty_input(self):
        assert _extract_arxiv_ids([]) == set()
        assert _extract_arxiv_ids(None) == set()

    def test_none_entries(self):
        assert _extract_arxiv_ids([None, None]) == set()

    def test_missing_external_ids(self):
        assert _extract_arxiv_ids([{}]) == set()
        assert _extract_arxiv_ids([{"externalIds": None}]) == set()


# ---------------------------------------------------------------------------
# Cache load / save
# ---------------------------------------------------------------------------


class TestCache:

    def test_load_missing_file(self, tmp_path):
        result = load_cache(str(tmp_path / "nonexistent.json"))
        assert result == {}

    def test_round_trip(self, tmp_path):
        path = str(tmp_path / "cache.json")
        data = {"2301.00001": {"references": ["2302.00002"], "cited_by": []}}
        save_cache(data, path)
        loaded = load_cache(path)
        assert loaded == data


# ---------------------------------------------------------------------------
# fetch_citations — mocked S2 API
# ---------------------------------------------------------------------------


class TestFetchCitations:

    def _make_s2_response(self, paper_id, refs=None, cites=None):
        """Build a fake S2 API entry."""
        return {
            "paperId": f"s2-{paper_id}",
            "references": [
                {"externalIds": {"ArXiv": rid}} for rid in (refs or [])
            ],
            "citations": [
                {"externalIds": {"ArXiv": cid}} for cid in (cites or [])
            ],
        }

    @patch("secnews.utils_citations.requests.Session")
    def test_incremental_fetch(self, mock_session_cls, tmp_path):
        """Only papers not in the cache should be fetched."""
        cache_path = str(tmp_path / "cache.json")
        # Pre-seed cache with one paper
        save_cache({"P1": {"references": [], "cited_by": []}}, cache_path)

        db_ids = {"P1", "P2", "P3"}
        paper_ids = sorted(db_ids)

        # Mock POST response — only P2 and P3 should be fetched
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            self._make_s2_response("P2", refs=["P1"]),
            self._make_s2_response("P3", refs=["P2"], cites=["P1"]),
        ]
        mock_session.post.return_value = mock_resp

        result = fetch_citations(paper_ids, db_ids, cache_path=cache_path)

        # All three papers should now be in the cache
        assert set(result.keys()) == {"P1", "P2", "P3"}
        # P2 references P1 (which is in DB)
        assert result["P2"]["references"] == ["P1"]
        # P3 references P2 and is cited by P1
        assert result["P3"]["references"] == ["P2"]
        assert result["P3"]["cited_by"] == ["P1"]

    @patch("secnews.utils_citations.requests.Session")
    def test_not_found_gets_empty_entry(self, mock_session_cls, tmp_path):
        """Papers not found in S2 get empty entries so they won't be re-queried."""
        cache_path = str(tmp_path / "cache.json")
        db_ids = {"P1"}

        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [None]  # paper not found
        mock_session.post.return_value = mock_resp

        result = fetch_citations(["P1"], db_ids, cache_path=cache_path)
        assert result["P1"] == {"references": [], "cited_by": []}

    @patch("secnews.utils_citations.requests.Session")
    def test_force_refetches_cached(self, mock_session_cls, tmp_path):
        """With force=True, cached papers are re-fetched."""
        cache_path = str(tmp_path / "cache.json")
        save_cache({"P1": {"references": ["old"], "cited_by": []}}, cache_path)

        db_ids = {"P1", "P2"}
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            self._make_s2_response("P1", refs=["P2"]),
        ]
        mock_session.post.return_value = mock_resp

        result = fetch_citations(["P1"], db_ids, cache_path=cache_path, force=True)
        assert result["P1"]["references"] == ["P2"]

    def test_fully_cached_skips_fetch(self, tmp_path):
        """If all papers are cached, no HTTP calls are made at all."""
        cache_path = str(tmp_path / "cache.json")
        save_cache({
            "P1": {"references": [], "cited_by": []},
            "P2": {"references": [], "cited_by": []},
        }, cache_path)

        # No mocking needed — should not hit the network
        result = fetch_citations(["P1", "P2"], {"P1", "P2"}, cache_path=cache_path)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# build_citation_edges
# ---------------------------------------------------------------------------


class TestBuildCitationEdges:

    def test_basic_edges(self):
        cache = {
            "A": {"references": ["B", "C"], "cited_by": []},
            "B": {"references": [], "cited_by": ["A"]},
            "C": {"references": [], "cited_by": ["A"]},
        }
        db_ids = {"A", "B", "C"}
        edges = build_citation_edges(cache, db_ids)
        pairs = {(e["source"], e["target"]) for e in edges}
        assert ("A", "B") in pairs
        assert ("A", "C") in pairs
        assert len(edges) == 2

    def test_filters_to_db_only(self):
        cache = {
            "A": {"references": ["B", "EXTERNAL"], "cited_by": []},
        }
        db_ids = {"A", "B"}
        edges = build_citation_edges(cache, db_ids)
        assert len(edges) == 1
        assert edges[0]["target"] == "B"

    def test_deduplicates(self):
        cache = {
            "A": {"references": ["B"], "cited_by": []},
        }
        edges = build_citation_edges(cache, {"A", "B"})
        assert len(edges) == 1


# ---------------------------------------------------------------------------
# _normalize_author
# ---------------------------------------------------------------------------


class TestNormalizeAuthor:

    def test_lowercase(self):
        assert _normalize_author("Alice Smith") == "alice smith"

    def test_strip_accents(self):
        assert _normalize_author("José García") == "jose garcia"

    def test_strip_et_al(self):
        assert _normalize_author("Smith et al.") == "smith"
        assert _normalize_author("Smith, et al.") == "smith"

    def test_whitespace(self):
        assert _normalize_author("  Bob  ") == "bob"


# ---------------------------------------------------------------------------
# build_author_edges
# ---------------------------------------------------------------------------


class TestBuildAuthorEdges:

    def test_shared_author_creates_edge(self):
        papers = [
            {"id": "A", "authors": ["Alice", "Bob"]},
            {"id": "B", "authors": ["Alice", "Charlie"]},
            {"id": "C", "authors": ["Dave"]},
        ]
        edges = build_author_edges(papers)
        # Only A–B share Alice
        assert len(edges) == 1
        e = edges[0]
        assert {e["source"], e["target"]} == {"A", "B"}
        assert e["weight"] == 1
        assert "Alice" in e["shared_authors"]

    def test_multiple_shared_authors(self):
        papers = [
            {"id": "A", "authors": ["Alice", "Bob"]},
            {"id": "B", "authors": ["Alice", "Bob"]},
        ]
        edges = build_author_edges(papers)
        assert len(edges) == 1
        assert edges[0]["weight"] == 2

    def test_no_shared_authors(self):
        papers = [
            {"id": "A", "authors": ["Alice"]},
            {"id": "B", "authors": ["Bob"]},
        ]
        assert build_author_edges(papers) == []

    def test_accent_normalization_links_papers(self):
        papers = [
            {"id": "A", "authors": ["José"]},
            {"id": "B", "authors": ["Jose"]},
        ]
        edges = build_author_edges(papers)
        assert len(edges) == 1

    def test_empty_authors(self):
        papers = [
            {"id": "A", "authors": []},
            {"id": "B"},
        ]
        assert build_author_edges(papers) == []
