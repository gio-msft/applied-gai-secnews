#!/usr/bin/env python3
"""Build the interactive paper graph visualization.

Reads papers.json, fetches citations via Semantic Scholar (incrementally
cached), computes author-overlap edges, pre-computes a ForceAtlas2 layout,
and writes docs/viz/data/graph.json consumed by the static frontend.
"""

import argparse
import json
import logging
import os
import sys
import unicodedata
from collections import defaultdict
from itertools import combinations
from pathlib import Path

from secnews.utils_citations import (
    build_citation_edges,
    fetch_citations,
    load_cache,
)
from secnews.utils_db import PaperDB

logger = logging.getLogger("AIRT-GAI-SecNews")
logger.setLevel(logging.DEBUG)
if not logger.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter(
        "\033[1;32m%(levelname)-5s %(module)s:%(funcName)s():"
        "%(lineno)d %(asctime)s\033[0m| %(message)s"
    ))
    logger.addHandler(_h)

DB_PATH = "papers.json"
CACHE_PATH = "citations_cache.json"
OUTPUT_PATH = "docs/viz/data/graph.json"


# ---------------------------------------------------------------------------
# Author-overlap helpers
# ---------------------------------------------------------------------------

def _normalize_author(name):
    """Lowercase, strip accents, drop 'et al.' suffix."""
    name = name.strip()
    if name.lower().endswith("et al."):
        name = name[: -len("et al.")].strip().rstrip(",")
    # Strip accents
    nfkd = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in nfkd if not unicodedata.combining(c))
    return name.lower()


def build_author_edges(papers):
    """Return author-overlap edges for a list of paper dicts.

    Each edge is ``{"source", "target", "weight", "shared_authors"}``.
    """
    # Inverted index: normalized_author → set(paper_ids)
    author_index = defaultdict(set)
    # Keep original name for display
    author_display = {}

    for p in papers:
        pid = p["id"]
        for raw in p.get("authors") or []:
            norm = _normalize_author(raw)
            if not norm:
                continue
            author_index[norm].add(pid)
            author_display[norm] = raw  # last-write wins, fine for display

    # For every author appearing in ≥2 papers, generate edges
    pair_authors = defaultdict(list)  # (id_a, id_b) → [author_names]
    for norm, pids in author_index.items():
        if len(pids) < 2:
            continue
        for a, b in combinations(sorted(pids), 2):
            pair_authors[(a, b)].append(author_display[norm])

    edges = []
    for (a, b), authors in pair_authors.items():
        edges.append({
            "source": a,
            "target": b,
            "weight": len(authors),
            "shared_authors": authors,
        })
    return edges


# ---------------------------------------------------------------------------
# Layout — pre-compute with graphology-like ForceAtlas2 via networkx spring
# ---------------------------------------------------------------------------

def _compute_layout(nodes, citation_edges, author_edges):
    """Compute (x, y) positions via a spring layout.

    Uses networkx spring_layout as a lightweight alternative to FA2.
    Falls back to random positions if networkx is not available.
    """
    try:
        import networkx as nx
    except ImportError:
        logger.warning("networkx not installed; using random layout. "
                       "Install networkx for better layout quality.")
        import random
        random.seed(42)
        return {n["id"]: {"x": random.uniform(-1, 1),
                          "y": random.uniform(-1, 1)} for n in nodes}

    G = nx.Graph()
    for n in nodes:
        G.add_node(n["id"])
    # Combine both edge sets for layout computation
    for e in citation_edges:
        G.add_edge(e["source"], e["target"], weight=1)
    for e in author_edges:
        G.add_edge(e["source"], e["target"], weight=e.get("weight", 1))

    logger.info("Computing layout for %d nodes, %d edges…",
                G.number_of_nodes(), G.number_of_edges())
    pos = nx.spring_layout(G, k=0.3, iterations=100, seed=42)
    return {nid: {"x": float(xy[0]), "y": float(xy[1])} for nid, xy in pos.items()}


# ---------------------------------------------------------------------------
# Main build
# ---------------------------------------------------------------------------

def build_graph(db_path=DB_PATH, cache_path=CACHE_PATH, output_path=OUTPUT_PATH,
                skip_citations=False, force_citations=False):
    """Build the graph JSON and write to *output_path*."""

    paper_db = PaperDB(db_path)
    all_papers = paper_db.find(summarized=True)
    logger.info("Found %d summarized papers.", len(all_papers))

    db_id_set = {p["id"] for p in all_papers}

    # --- Citation edges ---
    if skip_citations:
        logger.info("Skipping citation fetch (using cache only).")
        cache = load_cache(cache_path)
    else:
        paper_ids = sorted(db_id_set)
        cache = fetch_citations(
            paper_ids, db_id_set,
            cache_path=cache_path,
            force=force_citations,
        )

    citation_edges = build_citation_edges(cache, db_id_set)
    logger.info("Citation edges: %d", len(citation_edges))

    # --- Author edges ---
    author_edges = build_author_edges(all_papers)
    logger.info("Author-overlap edges: %d", len(author_edges))

    # --- Layout ---
    positions = _compute_layout(all_papers, citation_edges, author_edges)

    # --- Build nodes ---
    nodes = []
    for p in all_papers:
        pos = positions.get(p["id"], {"x": 0, "y": 0})
        nodes.append({
            "id": p["id"],
            "title": p.get("title", p["id"]),
            "authors": p.get("authors", []),
            "affiliations": p.get("affiliations", []),
            "url": p.get("url", ""),
            "published": p.get("published", ""),
            "emoji": p.get("emoji", "📄"),
            "tag": p.get("tag", "general"),
            "one_liner": p.get("one_liner", ""),
            "points": p.get("points", []),
            "interest_score": p.get("interest_score", 5),
            "projects": p.get("projects", []),
            "relevant": p.get("relevant", False),
            "x": pos["x"],
            "y": pos["y"],
        })

    graph = {
        "nodes": nodes,
        "citation_edges": citation_edges,
        "author_edges": author_edges,
    }

    # Ensure output directory exists
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(graph))
    size_mb = out.stat().st_size / (1024 * 1024)
    logger.info("Wrote %s (%.1f MB) — %d nodes, %d citation edges, %d author edges.",
                output_path, size_mb, len(nodes), len(citation_edges), len(author_edges))

    return graph


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Build interactive paper graph visualization data."
    )
    parser.add_argument(
        "--skip-citations", action="store_true",
        help="Use cached citations only; do not query Semantic Scholar.",
    )
    parser.add_argument(
        "--force-citations", action="store_true",
        help="Re-fetch all citations regardless of what is cached.",
    )
    parser.add_argument(
        "--db-path", default=DB_PATH,
        help="Path to papers.json (default: %(default)s).",
    )
    parser.add_argument(
        "--cache-path", default=CACHE_PATH,
        help="Path to citations cache (default: %(default)s).",
    )
    parser.add_argument(
        "--output-path", default=OUTPUT_PATH,
        help="Path to output graph.json (default: %(default)s).",
    )
    args = parser.parse_args()

    build_graph(
        db_path=args.db_path,
        cache_path=args.cache_path,
        output_path=args.output_path,
        skip_citations=args.skip_citations,
        force_citations=args.force_citations,
    )
