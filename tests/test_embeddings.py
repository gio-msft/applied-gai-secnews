"""Tests for the shared embedding infrastructure in build_viz.py."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from build_viz import _paper_text, compute_embeddings, EMBEDDING_DEPLOYMENT


# ---------------------------------------------------------------------------
# _paper_text
# ---------------------------------------------------------------------------

class TestPaperText:

    def test_full_record(self):
        paper = {
            "title": "Adversarial Prompt Injection",
            "one_liner": "A novel attack vector.",
            "points": ["Finding one.", "Finding two."],
        }
        result = _paper_text(paper)
        assert result == (
            "Adversarial Prompt Injection. A novel attack vector.. "
            "Finding one. Finding two."
        )

    def test_missing_fields_graceful(self):
        paper = {"id": "1234"}
        result = _paper_text(paper)
        assert result == ". . "

    def test_empty_points(self):
        paper = {"title": "Title", "one_liner": "Liner", "points": []}
        result = _paper_text(paper)
        assert result == "Title. Liner. "


# ---------------------------------------------------------------------------
# compute_embeddings
# ---------------------------------------------------------------------------

def _make_mock_client(embeddings_map):
    """Build a mock AzureOpenAI client that returns deterministic embeddings.

    *embeddings_map* maps input text → list[float].  For convenience, if a
    text isn't in the map, a vector of [0.0]*3 is returned.
    """
    client = MagicMock()

    def _create(*, model, input):  # noqa: A002
        assert model == EMBEDDING_DEPLOYMENT
        data = []
        for idx, text in enumerate(input):
            emb = MagicMock()
            emb.embedding = embeddings_map.get(text, [0.0] * 3)
            data.append(emb)
        resp = MagicMock()
        resp.data = data
        return resp

    client.embeddings.create = _create
    return client


class TestComputeEmbeddings:

    def test_cold_cache(self, tmp_path):
        """All papers are new — every one should be embedded and cached."""
        cache_path = str(tmp_path / "cache.json")
        papers = [
            {"id": "A", "title": "Paper A", "one_liner": "OL A", "points": ["P1"]},
            {"id": "B", "title": "Paper B", "one_liner": "OL B", "points": ["P2"]},
        ]
        client = _make_mock_client({})
        result = compute_embeddings(papers, client, cache_path=cache_path)

        assert set(result.keys()) == {"A", "B"}
        assert all(isinstance(v, list) for v in result.values())
        # Verify cache file was written
        cached = json.loads(Path(cache_path).read_text())
        assert cached == result

    def test_warm_cache_only_missing_embedded(self, tmp_path):
        """Papers already in cache should NOT be re-embedded."""
        cache_path = str(tmp_path / "cache.json")
        # Pre-populate cache with paper A
        Path(cache_path).write_text(json.dumps({"A": [1.0, 2.0, 3.0]}))

        papers = [
            {"id": "A", "title": "Paper A", "one_liner": "OL A", "points": ["P1"]},
            {"id": "B", "title": "Paper B", "one_liner": "OL B", "points": ["P2"]},
        ]

        call_log = []

        client = MagicMock()

        def _create(*, model, input):  # noqa: A002
            call_log.append(input)
            data = []
            for text in input:
                emb = MagicMock()
                emb.embedding = [4.0, 5.0, 6.0]
                data.append(emb)
            resp = MagicMock()
            resp.data = data
            return resp

        client.embeddings.create = _create

        result = compute_embeddings(papers, client, cache_path=cache_path)

        # A should retain its cached value
        assert result["A"] == [1.0, 2.0, 3.0]
        # B should have the newly computed value
        assert result["B"] == [4.0, 5.0, 6.0]
        # Only one API call, and only for B's text
        assert len(call_log) == 1
        assert len(call_log[0]) == 1  # one text in the batch

    def test_full_cache_no_api_call(self, tmp_path):
        """When all papers are cached, zero API calls should be made."""
        cache_path = str(tmp_path / "cache.json")
        Path(cache_path).write_text(
            json.dumps({"A": [1.0], "B": [2.0]})
        )
        papers = [
            {"id": "A", "title": "A", "one_liner": "", "points": []},
            {"id": "B", "title": "B", "one_liner": "", "points": []},
        ]
        client = MagicMock()
        result = compute_embeddings(papers, client, cache_path=cache_path)

        assert result == {"A": [1.0], "B": [2.0]}
        client.embeddings.create.assert_not_called()

    def test_cache_roundtrip(self, tmp_path):
        """Cache written by compute_embeddings can be loaded by a second call."""
        cache_path = str(tmp_path / "cache.json")
        papers = [
            {"id": "X", "title": "X", "one_liner": "x", "points": []},
        ]
        client = _make_mock_client({})
        # First call writes the cache
        result1 = compute_embeddings(papers, client, cache_path=cache_path)
        # Second call reads from cache (no API calls)
        client2 = MagicMock()
        result2 = compute_embeddings(papers, client2, cache_path=cache_path)

        assert result1 == result2
        client2.embeddings.create.assert_not_called()

    def test_batching_large_corpus(self, tmp_path):
        """When corpus exceeds EMBEDDING_BATCH_SIZE, multiple API calls are made."""
        cache_path = str(tmp_path / "cache.json")
        # Create papers exceeding batch size (use a small override via patching)
        papers = [{"id": str(i), "title": f"T{i}", "one_liner": "", "points": []}
                  for i in range(5)]

        call_count = []
        client = MagicMock()

        def _create(*, model, input):  # noqa: A002
            call_count.append(len(input))
            data = []
            for text in input:
                emb = MagicMock()
                emb.embedding = [0.1]
                data.append(emb)
            resp = MagicMock()
            resp.data = data
            return resp

        client.embeddings.create = _create

        with patch("build_viz.EMBEDDING_BATCH_SIZE", 2):
            result = compute_embeddings(papers, client, cache_path=cache_path)

        assert len(result) == 5
        # 5 papers / batch size 2 = 3 calls (2+2+1)
        assert call_count == [2, 2, 1]


# ---------------------------------------------------------------------------
# Integration test (requires live Azure OpenAI endpoint)
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestEmbeddingsIntegration:

    def test_real_embedding(self, tmp_path):
        """Hit the real Azure OpenAI endpoint with a single paper."""
        from build_viz import _make_oai_client

        cache_path = str(tmp_path / "cache.json")
        papers = [{
            "id": "test-001",
            "title": "Adversarial Prompt Injection in LLM Agents",
            "one_liner": "A novel attack vector for prompt injection.",
            "points": ["92% success rate on AgentBench."],
        }]
        client = _make_oai_client()
        result = compute_embeddings(papers, client, cache_path=cache_path)

        assert "test-001" in result
        vec = result["test-001"]
        assert isinstance(vec, list)
        assert len(vec) == 1536  # text-embedding-3-small dimension
        assert all(isinstance(v, float) for v in vec)
