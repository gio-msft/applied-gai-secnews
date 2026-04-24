#!/usr/bin/env python3
"""Build the interactive paper graph visualization.

Reads papers.json, fetches citations via Semantic Scholar (incrementally
cached), computes author-overlap edges, pre-computes a ForceAtlas2 layout,
and writes docs/data/graph.json consumed by the static frontend.
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

import numpy as np
import openai

import dotenv
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import AzureOpenAI

from secnews.utils_citations import (
    build_citation_edges,
    fetch_citations,
    load_cache,
)
from secnews.utils_db import PaperDB

dotenv.load_dotenv(".env")

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
OUTPUT_PATH = "docs/data/graph.json"
EMBEDDING_DEPLOYMENT = "text-embedding-3-large"
EMBEDDING_CACHE_PATH = "embeddings_cache.json"
EMBEDDING_BATCH_SIZE = 2048  # max inputs per API call
HDBSCAN_MIN_CLUSTER_SIZE = 3
PCA_COMPONENTS = 30
HDBSCAN_MIN_SAMPLES = 2
SIMILARITY_THRESHOLD = 0.55
SIMILARITY_MAX_K = 5
UMAP_STATE_PATH = "umap_state.json"
CLUSTER_MATCH_JACCARD_THRESHOLD = 0.5

# 15-color palette for topic regions (light/dark variants)
CLUSTER_COLORS = [
    {"light": "#4f6df5", "dark": "#6b8aff"},
    {"light": "#e67e22", "dark": "#f5a623"},
    {"light": "#27ae60", "dark": "#2ecc71"},
    {"light": "#e74c3c", "dark": "#ff6b6b"},
    {"light": "#9b59b6", "dark": "#bb86fc"},
    {"light": "#1abc9c", "dark": "#4fd1c5"},
    {"light": "#f39c12", "dark": "#fbbf24"},
    {"light": "#3498db", "dark": "#63b3ed"},
    {"light": "#e91e63", "dark": "#f48fb1"},
    {"light": "#00bcd4", "dark": "#4dd0e1"},
    {"light": "#8bc34a", "dark": "#aed581"},
    {"light": "#ff5722", "dark": "#ff8a65"},
    {"light": "#607d8b", "dark": "#90a4ae"},
    {"light": "#795548", "dark": "#a1887f"},
    {"light": "#673ab7", "dark": "#9575cd"},
]


# ---------------------------------------------------------------------------
# Embedding helpers
# ---------------------------------------------------------------------------

def _make_oai_client():
    """Create an AzureOpenAI client using DefaultAzureCredential."""
    credential = DefaultAzureCredential()
    token_provider = get_bearer_token_provider(
        credential, "https://cognitiveservices.azure.com/.default"
    )
    return AzureOpenAI(
        azure_endpoint=os.environ.get("AZURE_OPENAI_ENDPOINT"),
        azure_ad_token_provider=token_provider,
        api_version="2025-01-01-preview",
    )


def _paper_text(paper):
    """Build the text string used as embedding input for a paper."""
    title = paper.get("title", "")
    one_liner = paper.get("one_liner", "")
    points = " ".join(paper.get("points", []))
    return f"{title}. {one_liner}. {points}"


def compute_embeddings(papers, oai_client, cache_path=EMBEDDING_CACHE_PATH):
    """Return ``{paper_id: [float, …]}`` using cached + incremental API calls.

    Only papers whose IDs are missing from the cache are sent to the API.
    The cache file is updated on disk after new embeddings are fetched.
    """
    cache = (
        json.loads(Path(cache_path).read_text())
        if Path(cache_path).exists()
        else {}
    )
    missing = [p for p in papers if p["id"] not in cache]
    if missing:
        logger.info("Computing embeddings for %d new papers (%d cached).",
                    len(missing), len(cache))
        texts = [_paper_text(p) for p in missing]
        # Chunk into batches of EMBEDDING_BATCH_SIZE
        for i in range(0, len(texts), EMBEDDING_BATCH_SIZE):
            batch_texts = texts[i : i + EMBEDDING_BATCH_SIZE]
            batch_papers = missing[i : i + EMBEDDING_BATCH_SIZE]
            resp = oai_client.embeddings.create(
                model=EMBEDDING_DEPLOYMENT, input=batch_texts,
            )
            for p, emb in zip(batch_papers, resp.data):
                cache[p["id"]] = emb.embedding
        Path(cache_path).write_text(json.dumps(cache))
        logger.info("Embedding cache updated — %d total entries.", len(cache))
    else:
        logger.info("All %d embeddings cached; no API calls needed.", len(cache))
    return cache


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
# Topic clustering
# ---------------------------------------------------------------------------

def _match_clusters_to_previous(new_cluster_papers, prev_clusters,
                                threshold=CLUSTER_MATCH_JACCARD_THRESHOLD):
    """Match new HDBSCAN clusters to previous clusters by Jaccard similarity.

    *new_cluster_papers* is ``{cluster_id: [paper_id, ...]}``.
    *prev_clusters* is the ``topic_regions`` list from a previous graph.json.

    Returns a dict ``{new_cluster_id: {"label": str, "color": dict}}``
    for clusters that matched above *threshold*.  Unmatched clusters are
    omitted (the caller should generate fresh labels for those).
    """
    if not prev_clusters:
        return {}

    prev_sets = {r["id"]: set(r["papers"]) for r in prev_clusters}
    prev_meta = {r["id"]: {"label": r["label"], "color": r["color"]}
                 for r in prev_clusters}

    matched = {}          # new_cid → meta
    used_prev_ids = set()  # prevent two new clusters sharing a prev match

    for new_cid, new_pids in new_cluster_papers.items():
        new_set = set(new_pids)
        best_jaccard = 0.0
        best_prev_id = None

        for prev_id, prev_set in prev_sets.items():
            if prev_id in used_prev_ids:
                continue
            intersection = len(new_set & prev_set)
            union = len(new_set | prev_set)
            if union == 0:
                continue
            jaccard = intersection / union
            if jaccard > best_jaccard:
                best_jaccard = jaccard
                best_prev_id = prev_id

        if best_prev_id is not None and best_jaccard >= threshold:
            matched[new_cid] = prev_meta[best_prev_id]
            used_prev_ids.add(best_prev_id)

    return matched


def compute_topic_clusters(papers, embeddings, oai_client,
                           prev_clusters=None):
    """Cluster papers by embedding similarity and generate LLM labels.

    Returns a list of cluster dicts::

        [{"id": 0, "label": "...", "color": {"light": ..., "dark": ...},
          "papers": [paper_id, ...]}, ...]

    Also returns a dict mapping paper_id → cluster_id (or -1 for noise).
    """
    from sklearn.cluster import HDBSCAN
    from sklearn.decomposition import PCA

    paper_ids = [p["id"] for p in papers]
    matrix = np.array([embeddings[pid] for pid in paper_ids])

    # Dimensionality reduction
    n_components = min(PCA_COMPONENTS, len(paper_ids) - 1, matrix.shape[1])
    if n_components > 1:
        pca = PCA(n_components=n_components, random_state=42)
        reduced = pca.fit_transform(matrix)
        logger.info("PCA: %d dims → %d (%.1f%% variance retained).",
                    matrix.shape[1], n_components,
                    pca.explained_variance_ratio_.sum() * 100)
    else:
        reduced = matrix

    # Clustering
    clusterer = HDBSCAN(
        min_cluster_size=HDBSCAN_MIN_CLUSTER_SIZE,
        min_samples=HDBSCAN_MIN_SAMPLES,
    )
    labels = clusterer.fit_predict(reduced)
    unique_labels = sorted(int(l) for l in set(labels) - {-1})
    logger.info("HDBSCAN found %d clusters (%d noise papers).",
                len(unique_labels), int((labels == -1).sum()))

    # Build cluster membership
    paper_cluster = {}  # paper_id → cluster_id
    cluster_papers = defaultdict(list)  # cluster_id → [paper_id]
    for pid, lbl in zip(paper_ids, labels):
        paper_cluster[pid] = int(lbl)
        if lbl != -1:
            cluster_papers[int(lbl)].append(pid)

    # Build paper lookup for label generation
    paper_by_id = {p["id"]: p for p in papers}

    # Match new clusters to previous ones to reuse stable labels
    matched_meta = _match_clusters_to_previous(
        dict(cluster_papers), prev_clusters or [],
    )
    n_reused = len(matched_meta)
    n_new_labels = len(unique_labels) - n_reused
    if prev_clusters:
        logger.info(
            "Cluster label matching: %d reused, %d new (of %d total).",
            n_reused, n_new_labels, len(unique_labels),
        )

    # Generate LLM labels only for unmatched clusters
    clusters = []
    for i, cid in enumerate(unique_labels):
        members = cluster_papers[cid]

        if cid in matched_meta:
            label = matched_meta[cid]["label"]
            color = matched_meta[cid]["color"]
            logger.info("Cluster %d (%d papers): %s [reused]",
                        cid, len(members), label)
        else:
            color = CLUSTER_COLORS[i % len(CLUSTER_COLORS)]
            # Build text summary for LLM
            sample_texts = []
            for pid in members[:20]:  # cap at 20 to keep prompt short
                p = paper_by_id[pid]
                sample_texts.append(
                    f"- {p.get('title', pid)}: {p.get('one_liner', '')}")
            sample_block = "\n".join(sample_texts)

            label = _generate_cluster_label(oai_client, sample_block)
            logger.info("Cluster %d (%d papers): %s [new]",
                        cid, len(members), label)

        clusters.append({
            "id": cid,
            "label": label,
            "color": color,
            "papers": members,
        })

    return clusters, paper_cluster


def _generate_cluster_label(oai_client, sample_block):
    """Ask the LLM for a short topic label given paper summaries."""
    try:
        resp = oai_client.chat.completions.create(
            model=os.environ.get("AZURE_OPENAI_SUMMARY_MODEL_NAME"),
            messages=[
                {"role": "system", "content": (
                    "You are a research librarian. Given paper titles and summaries "
                    "from a single thematic cluster, produce a short topic label "
                    "(2-5 words). Return ONLY the label, nothing else."
                )},
                {"role": "user", "content": sample_block},
            ],
            max_completion_tokens=20,
            temperature=0.3,
        )
        return resp.choices[0].message.content.strip().strip('"').strip("'")
    except openai.BadRequestError as exc:
        logger.warning("Content filter triggered for cluster label, using fallback: %s", exc)
        return _fallback_cluster_label(sample_block)


def _fallback_cluster_label(sample_block):
    """Extract a label from paper titles using keyword frequency."""
    from collections import Counter
    stopwords = {
        "a", "an", "the", "and", "or", "of", "in", "on", "for", "to", "with",
        "by", "from", "is", "are", "its", "via", "using", "based", "towards",
        "against", "through", "into", "over", "how", "can", "do", "does", "not",
        "their", "your", "we", "our", "as", "at", "be", "it", "that", "this",
        "an", "no", "but", "what", "when", "where", "which", "who", "new",
    }
    words = Counter()
    for line in sample_block.splitlines():
        # Lines are formatted as "- Title: one_liner" — extract the title part
        title = line.lstrip("- ").split(":")[0]
        for word in title.split():
            w = word.strip("(),\"'").lower()
            if len(w) > 2 and w not in stopwords:
                words[w] += 1
    top = [w for w, _ in words.most_common(3)]
    label = " ".join(top).title() if top else "Research Cluster"
    logger.info("Fallback label generated: %s", label)
    return label


# ---------------------------------------------------------------------------
# Similarity edges
# ---------------------------------------------------------------------------

def compute_similarity_edges(papers, embeddings,
                             threshold=SIMILARITY_THRESHOLD,
                             max_k=SIMILARITY_MAX_K):
    """Compute pairwise cosine similarity and return top-k edges.

    Returns a list of ``{"source", "target", "weight"}`` dicts.
    """
    from sklearn.metrics.pairwise import cosine_similarity

    paper_ids = [p["id"] for p in papers]
    matrix = np.array([embeddings[pid] for pid in paper_ids])
    sim_matrix = cosine_similarity(matrix)

    edges = []
    seen = set()
    for i, pid_a in enumerate(paper_ids):
        # Get top-k indices (excluding self)
        row = sim_matrix[i].copy()
        row[i] = -1  # exclude self
        top_indices = np.argsort(row)[::-1][:max_k]
        for j in top_indices:
            if row[j] < threshold:
                continue
            pid_b = paper_ids[j]
            pair = tuple(sorted((pid_a, pid_b)))
            if pair in seen:
                continue
            seen.add(pair)
            edges.append({
                "source": pid_a,
                "target": pid_b,
                "weight": round(float(row[j]), 4),
            })

    logger.info("Similarity edges: %d (threshold=%.2f, max_k=%d).",
                len(edges), threshold, max_k)
    return edges


# ---------------------------------------------------------------------------
# Convex hull computation for topic regions
# ---------------------------------------------------------------------------

def _densest_centroid(pts, radius=0.04):
    """Return the centroid of the largest connected sub-group of *pts*.

    Two points are considered connected when their buffered circles
    (of the given *radius*) overlap, i.e. they are within ``2 * radius``
    of each other.  The label is placed at the mean of the largest
    connected component so it sits next to the biggest cluster of points
    rather than in empty space between disjoint sub-groups.
    """
    from scipy.spatial.distance import cdist
    from scipy.sparse.csgraph import connected_components
    from scipy.sparse import csr_matrix

    arr = np.array(pts)
    n = len(arr)
    if n <= 2:
        cx = round(float(arr[:, 0].mean()), 6)
        cy = round(float(arr[:, 1].mean()), 6)
        return cx, cy

    # Build adjacency: points whose buffer circles overlap
    dists = cdist(arr, arr)
    adjacency = (dists <= 2 * radius).astype(np.int8)
    np.fill_diagonal(adjacency, 0)

    n_components, labels = connected_components(
        csr_matrix(adjacency), directed=False
    )

    # Pick the largest component; if all components are the same size
    # (i.e. every point is isolated), fall back to the global mean.
    if n_components == 1:
        biggest = np.arange(n)
    else:
        counts = np.bincount(labels)
        max_count = counts.max()
        if max_count == 1:
            # All points are isolated — no dominant sub-group
            biggest = np.arange(n)
        else:
            biggest = np.where(labels == counts.argmax())[0]

    cx = round(float(arr[biggest, 0].mean()), 6)
    cy = round(float(arr[biggest, 1].mean()), 6)
    return cx, cy


def compute_hulls(clusters, positions, radius=0.04, resolution=24, *, pad=None):
    """Compute smooth bubble outlines for each cluster using buffered union.

    Places a circle of *radius* around each node, merges them via
    ``shapely.ops.unary_union``, and extracts the resulting smooth boundary.
    Supports multi-polygon output if a cluster splits into sub-groups.

    *positions* is ``{paper_id: {"x": float, "y": float}}``.

    *pad* is accepted as a legacy alias for *radius*.

    Mutates each cluster dict in-place, adding ``"rings"`` (list of
    coordinate rings), ``"hull"`` (first/largest ring, for backward compat),
    and ``"centroid"`` keys.
    """
    if pad is not None:
        radius = pad
    from shapely.geometry import Point, MultiPolygon
    from shapely.ops import unary_union

    for cluster in clusters:
        members = cluster["papers"]
        pts = [
            (positions[pid]["x"], positions[pid]["y"])
            for pid in members if pid in positions
        ]
        if len(pts) < 2:
            cluster["hull"] = []
            cluster["rings"] = []
            cx = float(pts[0][0]) if pts else 0.0
            cy = float(pts[0][1]) if pts else 0.0
            cluster["centroid"] = [cx, cy]
            continue

        # Buffer each point and merge
        circles = [Point(x, y).buffer(radius, resolution=resolution)
                   for x, y in pts]
        blob = unary_union(circles)

        # Extract rings from the resulting geometry
        rings = []
        polys = []
        if blob.geom_type == "Polygon":
            polys = [blob]
        elif blob.geom_type == "MultiPolygon":
            polys = list(blob.geoms)

        for poly in polys:
            coords = list(poly.exterior.coords)
            ring = [[round(x, 6), round(y, 6)] for x, y in coords]
            rings.append(ring)

        # Place label at the densest sub-region rather than the
        # geometric centroid, which can land in empty space.
        cx, cy = _densest_centroid(pts, radius=radius)

        cluster["rings"] = rings
        cluster["hull"] = rings[0] if rings else []  # backward compat
        cluster["centroid"] = [cx, cy]


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

def _compute_umap_layout(nodes, embeddings, min_dist=0.3, spread=1.5, seed=43,
                         prev_raw_positions=None, umap_state_path=UMAP_STATE_PATH):
    """Compute 2D positions via UMAP on the embedding vectors.

    If *prev_raw_positions* is provided (``{paper_id: [x, y]}`` in raw UMAP
    space), existing papers are warm-started at their previous coordinates
    and new papers are initialised near their nearest semantic neighbour.
    This keeps existing nodes visually stable across incremental rebuilds.

    Raw (pre-normalisation) coordinates are saved to *umap_state_path* so
    subsequent runs can warm-start from them.
    """
    from umap import UMAP
    from sklearn.metrics.pairwise import cosine_similarity as _cos_sim

    ids = [n["id"] for n in nodes]
    matrix = np.array([embeddings[pid] for pid in ids])

    # Build init array for warm-start when previous positions exist
    init = "spectral"  # UMAP default
    if prev_raw_positions:
        known_mask = [pid in prev_raw_positions for pid in ids]
        n_known = sum(known_mask)
        n_new = len(ids) - n_known
        if n_known >= 2:
            logger.info(
                "Warm-starting UMAP: %d existing nodes pinned, %d new nodes.",
                n_known, n_new,
            )
            rng = np.random.RandomState(seed)
            init_arr = np.zeros((len(ids), 2), dtype=np.float32)

            # Fill known positions
            known_indices = []
            known_embeddings = []
            for i, pid in enumerate(ids):
                if pid in prev_raw_positions:
                    init_arr[i] = prev_raw_positions[pid]
                    known_indices.append(i)
                    known_embeddings.append(matrix[i])

            # For new papers, find nearest known neighbour and place nearby
            if n_new > 0 and known_embeddings:
                known_emb_matrix = np.array(known_embeddings)
                for i, pid in enumerate(ids):
                    if pid not in prev_raw_positions:
                        sims = _cos_sim(matrix[i:i+1], known_emb_matrix)[0]
                        best_j = known_indices[int(np.argmax(sims))]
                        jitter = rng.normal(0, 0.1, size=2).astype(np.float32)
                        init_arr[i] = init_arr[best_j] + jitter

            init = init_arr
        else:
            logger.info(
                "Too few previous positions (%d); using default spectral init.",
                n_known,
            )

    logger.info("Running UMAP on %d × %d embedding matrix…", *matrix.shape)
    reducer = UMAP(
        n_components=2,
        min_dist=min_dist,
        spread=spread,
        metric="cosine",
        random_state=seed,
        init=init,
    )
    coords = reducer.fit_transform(matrix)

    # Save raw coordinates before normalisation
    raw_positions = {pid: [float(coords[i, 0]), float(coords[i, 1])]
                     for i, pid in enumerate(ids)}
    Path(umap_state_path).write_text(json.dumps(raw_positions))
    logger.info("Saved raw UMAP state to %s (%d entries).",
                umap_state_path, len(raw_positions))

    # Normalise to [-1, 1]
    for dim in range(2):
        lo, hi = coords[:, dim].min(), coords[:, dim].max()
        if hi - lo > 0:
            coords[:, dim] = 2 * (coords[:, dim] - lo) / (hi - lo) - 1

    return {pid: {"x": float(coords[i, 0]), "y": float(coords[i, 1])}
            for i, pid in enumerate(ids)}


def _compute_layout(nodes, edge_sets, k=1.5, iterations=200, seed=42):
    """Compute (x, y) positions via a spring layout.

    *edge_sets* is a list of ``(edges, weight_multiplier)`` tuples.
    Uses networkx spring_layout as a lightweight alternative to FA2.
    Falls back to random positions if networkx is not available.
    """
    try:
        import networkx as nx
    except ImportError:
        logger.warning("networkx not installed; using random layout. "
                       "Install networkx for better layout quality.")
        import random
        random.seed(seed)
        return {n["id"]: {"x": random.uniform(-1, 1),
                          "y": random.uniform(-1, 1)} for n in nodes}

    G = nx.Graph()
    for n in nodes:
        G.add_node(n["id"])
    for edges, multiplier in edge_sets:
        for e in edges:
            w = e.get("weight", 1) * multiplier
            src, tgt = e["source"], e["target"]
            if G.has_edge(src, tgt):
                G[src][tgt]["weight"] = G[src][tgt].get("weight", 0) + w
            else:
                G.add_edge(src, tgt, weight=w)

    logger.info("Computing layout for %d nodes, %d edges…",
                G.number_of_nodes(), G.number_of_edges())
    pos = nx.spring_layout(G, k=k, iterations=iterations, seed=seed)
    return {nid: {"x": float(xy[0]), "y": float(xy[1])} for nid, xy in pos.items()}


# ---------------------------------------------------------------------------
# Main build
# ---------------------------------------------------------------------------

def build_graph(db_path=DB_PATH, cache_path=CACHE_PATH, output_path=OUTPUT_PATH,
                skip_citations=False, force_citations=False,
                skip_embeddings=False, embedding_cache_path=EMBEDDING_CACHE_PATH,
                reuse_clusters=False, full_recompute=False,
                umap_state_path=UMAP_STATE_PATH):
    """Build the graph JSON and write to *output_path*.

    By default, the build is **incremental**: if previous UMAP state and
    graph data exist, UMAP is warm-started so existing node positions stay
    stable, and HDBSCAN cluster labels are reused for clusters whose
    membership hasn't changed much.

    Pass *full_recompute=True* (``--full-recompute``) to discard previous
    state and rebuild everything from scratch (recommended monthly).
    """

    paper_db = PaperDB(db_path)
    all_papers = paper_db.find(summarized=True)
    all_papers = [p for p in all_papers if "interest_score" in p and p["interest_score"] >= 5]
    logger.info("Found %d scored papers with score >= 5.", len(all_papers))

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

    # --- Embeddings (shared infrastructure for topic regions + similarity) ---
    embeddings = None
    if not skip_embeddings:
        oai_client = _make_oai_client()
        embeddings = compute_embeddings(
            all_papers, oai_client, cache_path=embedding_cache_path,
        )

    # --- Load previous state for incremental mode ---
    prev_raw_positions = None
    prev_clusters = None
    if not full_recompute:
        if Path(umap_state_path).exists():
            prev_raw_positions = json.loads(
                Path(umap_state_path).read_text())
            logger.info("Loaded previous UMAP state (%d entries) for warm-start.",
                        len(prev_raw_positions))
        if Path(output_path).exists():
            prev_graph = json.loads(Path(output_path).read_text())
            prev_clusters = prev_graph.get("topic_regions", [])
            if prev_clusters:
                logger.info("Loaded %d previous clusters for label matching.",
                            len(prev_clusters))
    else:
        logger.info("Full recompute requested — ignoring previous state.")

    # --- Semantic features (require embeddings) ---
    similarity_edges = []
    topic_regions = []
    paper_cluster = {}  # paper_id → cluster_id
    semantic_positions = {}  # paper_id → {x, y}

    if embeddings:
        # Topic clusters
        if reuse_clusters and Path(output_path).exists():
            logger.info("Reusing clusters from existing %s.", output_path)
            prev = json.loads(Path(output_path).read_text())
            topic_regions = [
                {"id": r["id"], "label": r["label"], "color": r["color"],
                 "papers": r["papers"]}
                for r in prev.get("topic_regions", [])
            ]
            paper_cluster = {}
            for r in topic_regions:
                for pid in r["papers"]:
                    paper_cluster[pid] = r["id"]
        else:
            topic_regions, paper_cluster = compute_topic_clusters(
                all_papers, embeddings, oai_client,
                prev_clusters=prev_clusters,
            )
        # Similarity edges
        similarity_edges = compute_similarity_edges(all_papers, embeddings)

    # --- Layout (structural: citations + authors) ---
    positions = _compute_layout(all_papers, [
        (citation_edges, 1.0),
        (author_edges, 1.0),
    ])

    # --- Semantic layout (UMAP on embedding vectors) ---
    if embeddings:
        semantic_positions = _compute_umap_layout(
            all_papers, embeddings,
            prev_raw_positions=prev_raw_positions,
            umap_state_path=umap_state_path,
        )

        # Compute convex hulls using semantic positions
        compute_hulls(topic_regions, semantic_positions)
    else:
        semantic_positions = positions  # fallback: same as structural

    # --- Build nodes ---
    nodes = []
    for p in all_papers:
        pos = positions.get(p["id"], {"x": 0, "y": 0})
        sem = semantic_positions.get(p["id"], pos)
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
            "semantic_x": sem["x"],
            "semantic_y": sem["y"],
            "cluster": paper_cluster.get(p["id"], -1),
        })

    graph = {
        "nodes": nodes,
        "citation_edges": citation_edges,
        "author_edges": author_edges,
        "similarity_edges": similarity_edges,
        "topic_regions": topic_regions,
    }

    # Ensure output directory exists
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(graph))
    size_mb = out.stat().st_size / (1024 * 1024)
    logger.info(
        "Wrote %s (%.1f MB) — %d nodes, %d citation, %d author, "
        "%d similarity edges, %d topic regions.",
        output_path, size_mb, len(nodes), len(citation_edges),
        len(author_edges), len(similarity_edges), len(topic_regions),
    )

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
        "--skip-embeddings", action="store_true",
        help="Skip embedding computation (no Azure OpenAI calls for embeddings).",
    )
    parser.add_argument(
        "--reuse-clusters", action="store_true",
        help="Reuse topic clusters/labels from existing graph.json; only recompute hulls.",
    )
    parser.add_argument(
        "--full-recompute", action="store_true",
        help="Ignore previous UMAP state and cluster labels; rebuild from scratch.",
    )
    parser.add_argument(
        "--umap-state-path", default=UMAP_STATE_PATH,
        help="Path to UMAP state cache (default: %(default)s).",
    )
    parser.add_argument(
        "--embedding-cache-path", default=EMBEDDING_CACHE_PATH,
        help="Path to embeddings cache (default: %(default)s).",
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
        skip_embeddings=args.skip_embeddings,
        embedding_cache_path=args.embedding_cache_path,
        reuse_clusters=args.reuse_clusters,
        full_recompute=args.full_recompute,
        umap_state_path=args.umap_state_path,
    )
