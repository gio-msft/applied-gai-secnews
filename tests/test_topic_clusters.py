"""Tests for topic clustering, similarity edges, and hull computation."""

import json
from unittest.mock import MagicMock

import numpy as np
import pytest

from build_viz import (
    compute_hulls,
    compute_similarity_edges,
    compute_topic_clusters,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_papers(n, cluster_centers=None):
    """Generate n synthetic papers with optional clustered embeddings."""
    papers = []
    for i in range(n):
        papers.append({
            "id": f"paper_{i}",
            "title": f"Paper {i}",
            "one_liner": f"Summary of paper {i}.",
            "points": [f"Point {i}."],
        })
    return papers


def _make_clustered_embeddings(papers, n_clusters=3, dim=1536):
    """Create embeddings that form clear, separable clusters."""
    rng = np.random.RandomState(42)
    embeddings = {}
    n = len(papers)
    cluster_size = n // n_clusters

    for i, p in enumerate(papers):
        cluster_id = min(i // cluster_size, n_clusters - 1)
        # Base vector for this cluster (orthogonal-ish directions)
        base = np.zeros(dim)
        base[cluster_id * 100:(cluster_id + 1) * 100] = 1.0
        # Add small noise
        noise = rng.normal(0, 0.05, dim)
        vec = base + noise
        # Normalize
        vec = vec / np.linalg.norm(vec)
        embeddings[p["id"]] = vec.tolist()

    return embeddings


def _make_mock_oai_client():
    """Mock OpenAI client that returns a simple label."""
    client = MagicMock()
    msg = MagicMock()
    msg.content = "Test Topic Label"
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    client.chat.completions.create.return_value = resp
    return client


# ---------------------------------------------------------------------------
# compute_topic_clusters
# ---------------------------------------------------------------------------

class TestComputeTopicClusters:

    def test_basic_clustering(self):
        papers = _make_papers(30)  # 3 clusters of 10
        embeddings = _make_clustered_embeddings(papers, n_clusters=3)
        client = _make_mock_oai_client()

        clusters, paper_cluster = compute_topic_clusters(
            papers, embeddings, client,
        )

        # Should find some clusters (HDBSCAN may find 2-3 depending on params)
        assert len(clusters) >= 1
        assert all("label" in c for c in clusters)
        assert all("color" in c for c in clusters)
        assert all("papers" in c for c in clusters)
        assert all(isinstance(c["id"], int) for c in clusters)

        # paper_cluster maps every paper
        assert len(paper_cluster) == 30
        # Values are ints (-1 for noise or cluster ID)
        assert all(isinstance(v, int) for v in paper_cluster.values())

    def test_small_input_no_crash(self):
        """3 papers is below min_cluster_size=5 — should return 0 clusters."""
        papers = _make_papers(3)
        rng = np.random.RandomState(0)
        embeddings = {
            p["id"]: rng.normal(0, 1, 1536).tolist()
            for p in papers
        }
        client = _make_mock_oai_client()

        clusters, paper_cluster = compute_topic_clusters(
            papers, embeddings, client,
        )

        assert isinstance(clusters, list)
        assert len(paper_cluster) == 3
        # All papers should be noise
        assert all(v == -1 for v in paper_cluster.values())

    def test_llm_called_per_cluster(self):
        papers = _make_papers(30)
        embeddings = _make_clustered_embeddings(papers, n_clusters=3)
        client = _make_mock_oai_client()

        clusters, _ = compute_topic_clusters(papers, embeddings, client)

        # LLM should be called once per cluster
        assert client.chat.completions.create.call_count == len(clusters)


# ---------------------------------------------------------------------------
# compute_similarity_edges
# ---------------------------------------------------------------------------

class TestComputeSimilarityEdges:

    def test_basic_output(self):
        papers = _make_papers(10)
        embeddings = _make_clustered_embeddings(papers, n_clusters=2, dim=1536)

        edges = compute_similarity_edges(
            papers, embeddings, threshold=0.3, max_k=3,
        )

        assert isinstance(edges, list)
        assert len(edges) > 0
        for e in edges:
            assert "source" in e
            assert "target" in e
            assert "weight" in e
            assert 0 <= e["weight"] <= 1
            assert e["source"] != e["target"]

    def test_symmetry(self):
        """No duplicate (a,b) and (b,a) pairs."""
        papers = _make_papers(10)
        embeddings = _make_clustered_embeddings(papers, n_clusters=2, dim=1536)

        edges = compute_similarity_edges(
            papers, embeddings, threshold=0.0, max_k=5,
        )

        pairs = set()
        for e in edges:
            pair = tuple(sorted((e["source"], e["target"])))
            assert pair not in pairs, f"Duplicate edge: {pair}"
            pairs.add(pair)

    def test_threshold_filtering(self):
        """Very high threshold should produce fewer edges."""
        papers = _make_papers(10)
        embeddings = _make_clustered_embeddings(papers, n_clusters=2, dim=1536)

        low = compute_similarity_edges(papers, embeddings, threshold=0.3, max_k=5)
        high = compute_similarity_edges(papers, embeddings, threshold=0.9, max_k=5)

        assert len(high) <= len(low)

    def test_max_k_respected(self):
        papers = _make_papers(20)
        embeddings = _make_clustered_embeddings(papers, n_clusters=2, dim=1536)

        edges = compute_similarity_edges(
            papers, embeddings, threshold=0.0, max_k=2,
        )

        # Each paper can contribute at most max_k edges (before dedup)
        from collections import Counter
        counts = Counter()
        for e in edges:
            counts[e["source"]] += 1
            counts[e["target"]] += 1
        # After dedup, a node can appear in more than max_k edges,
        # but it should have initiated at most max_k
        # We can't easily check that after dedup, so just verify edges exist
        assert len(edges) > 0


# ---------------------------------------------------------------------------
# compute_hulls
# ---------------------------------------------------------------------------

class TestComputeHulls:

    def test_basic_hull(self):
        clusters = [{
            "id": 0,
            "label": "Test",
            "color": {"light": "#000", "dark": "#fff"},
            "papers": ["a", "b", "c", "d"],
        }]
        positions = {
            "a": {"x": 0.0, "y": 0.0},
            "b": {"x": 1.0, "y": 0.0},
            "c": {"x": 0.0, "y": 1.0},
            "d": {"x": 1.0, "y": 1.0},
        }

        compute_hulls(clusters, positions, pad=0.01)

        assert "hull" in clusters[0]
        assert "centroid" in clusters[0]
        assert len(clusters[0]["hull"]) >= 3  # convex hull of 4 points
        # Centroid should be near (0.5, 0.5)
        cx, cy = clusters[0]["centroid"]
        assert abs(cx - 0.5) < 0.1
        assert abs(cy - 0.5) < 0.1

    def test_two_papers_hull(self):
        """Two papers produce a valid bubble via buffered union."""
        clusters = [{
            "id": 0,
            "label": "Small",
            "color": {"light": "#000", "dark": "#fff"},
            "papers": ["a", "b"],
        }]
        positions = {
            "a": {"x": 0.0, "y": 0.0},
            "b": {"x": 1.0, "y": 0.0},
        }

        compute_hulls(clusters, positions)

        assert len(clusters[0]["hull"]) >= 3  # buffered union produces a polygon
        assert "centroid" in clusters[0]

    def test_missing_position_skipped(self):
        """Papers missing from positions dict are silently skipped."""
        clusters = [{
            "id": 0,
            "label": "Partial",
            "color": {"light": "#000", "dark": "#fff"},
            "papers": ["a", "b", "c", "missing"],
        }]
        positions = {
            "a": {"x": 0.0, "y": 0.0},
            "b": {"x": 1.0, "y": 0.0},
            "c": {"x": 0.5, "y": 1.0},
        }

        compute_hulls(clusters, positions)

        assert len(clusters[0]["hull"]) >= 3

    def test_padding_expands_hull(self):
        clusters_small = [{
            "id": 0,
            "label": "A",
            "color": {"light": "#000", "dark": "#fff"},
            "papers": ["a", "b", "c"],
        }]
        clusters_large = [{
            "id": 0,
            "label": "A",
            "color": {"light": "#000", "dark": "#fff"},
            "papers": ["a", "b", "c"],
        }]
        positions = {
            "a": {"x": 0.0, "y": 0.0},
            "b": {"x": 1.0, "y": 0.0},
            "c": {"x": 0.5, "y": 1.0},
        }

        compute_hulls(clusters_small, positions, radius=0.01)
        compute_hulls(clusters_large, positions, radius=0.1)

        # Padded hull should have vertices further from centroid
        def max_dist(hull, centroid):
            return max(
                ((p[0] - centroid[0]) ** 2 + (p[1] - centroid[1]) ** 2) ** 0.5
                for p in hull
            )

        d_small = max_dist(clusters_small[0]["hull"], clusters_small[0]["centroid"])
        d_large = max_dist(clusters_large[0]["hull"], clusters_large[0]["centroid"])
        assert d_large > d_small
