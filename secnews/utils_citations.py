"""Semantic Scholar citation fetcher with persistent JSON cache.

Fetches citation/reference data for arXiv papers via the Semantic Scholar
batch API, filtering to papers that exist in our local DB.  Results are
cached in ``citations_cache.json`` so that only new papers are fetched on
subsequent runs.
"""

import json
import logging
import os
import time
from pathlib import Path

import requests

logger = logging.getLogger("AIRT-GAI-SecNews")

S2_BATCH_URL = "https://api.semanticscholar.org/graph/v1/paper/batch"
S2_BATCH_SIZE = 500  # API max per request
S2_FIELDS = "references.externalIds,citations.externalIds"
S2_RATE_LIMIT_DELAY = 1.0  # seconds between requests
S2_MAX_RETRIES = 5


def _s2_headers():
    """Return request headers, including API key if available."""
    headers = {"Content-Type": "application/json"}
    api_key = os.environ.get("S2_API_KEY")
    if api_key:
        headers["x-api-key"] = api_key
    return headers


def _extract_arxiv_ids(id_list):
    """Extract arXiv IDs from a list of S2 externalIds dicts."""
    arxiv_ids = set()
    if not id_list:
        return arxiv_ids
    for entry in id_list:
        if not entry:
            continue
        ext = entry.get("externalIds") or {}
        aid = ext.get("ArXiv")
        if aid:
            arxiv_ids.add(aid)
    return arxiv_ids


def load_cache(cache_path):
    """Load the citation cache from disk.  Returns empty dict if missing."""
    p = Path(cache_path)
    if p.exists():
        return json.loads(p.read_text())
    return {}


def save_cache(cache, cache_path):
    """Persist the citation cache to disk."""
    Path(cache_path).write_text(json.dumps(cache, indent=2))


def _fetch_batch(arxiv_ids, session):
    """Call the S2 batch endpoint for a list of arXiv IDs.

    Returns the raw JSON response list (one entry per requested ID,
    ``None`` for papers not found).
    """
    payload = {"ids": [f"ArXiv:{aid}" for aid in arxiv_ids]}
    for attempt in range(S2_MAX_RETRIES):
        resp = session.post(
            S2_BATCH_URL,
            params={"fields": S2_FIELDS},
            json=payload,
            headers=_s2_headers(),
            timeout=30,
        )
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code in (429, 503):
            wait = S2_RATE_LIMIT_DELAY * (2 ** attempt)
            logger.warning("S2 rate-limited (%s), retrying in %.1fs…", resp.status_code, wait)
            time.sleep(wait)
            continue
        resp.raise_for_status()
    logger.error("S2 batch request failed after %d retries", S2_MAX_RETRIES)
    return [None] * len(arxiv_ids)


def fetch_citations(paper_ids, db_id_set, cache_path="citations_cache.json",
                    force=False):
    """Fetch citation data for *paper_ids* that are not yet cached.

    Parameters
    ----------
    paper_ids : list[str]
        arXiv IDs of all summarized papers.
    db_id_set : set[str]
        Set of all paper IDs in the DB — used to filter edges to
        papers we actually have.
    cache_path : str
        Path to the persistent JSON cache file.
    force : bool
        If True, re-fetch everything regardless of what is cached.

    Returns
    -------
    dict
        The full citation cache (paper_id → {references, cited_by}).
    """
    cache = {} if force else load_cache(cache_path)

    to_fetch = [pid for pid in paper_ids if pid not in cache]
    if not to_fetch:
        logger.info("Citation cache is up-to-date (%d papers cached).", len(cache))
        return cache

    logger.info("Fetching citations for %d new papers (%d already cached)…",
                len(to_fetch), len(cache))

    session = requests.Session()
    for batch_start in range(0, len(to_fetch), S2_BATCH_SIZE):
        batch = to_fetch[batch_start:batch_start + S2_BATCH_SIZE]
        logger.info("  S2 batch %d–%d of %d…",
                     batch_start + 1, batch_start + len(batch), len(to_fetch))

        results = _fetch_batch(batch, session)

        for arxiv_id, result in zip(batch, results):
            if result is None:
                # Paper not found in S2 — store empty so we don't re-query
                cache[arxiv_id] = {"references": [], "cited_by": []}
                continue

            refs = _extract_arxiv_ids(result.get("references") or [])
            cites = _extract_arxiv_ids(result.get("citations") or [])

            # Keep only IDs that exist in our DB
            cache[arxiv_id] = {
                "references": sorted(refs & db_id_set),
                "cited_by": sorted(cites & db_id_set),
            }

        save_cache(cache, cache_path)

        # Rate-limit between batches
        if batch_start + S2_BATCH_SIZE < len(to_fetch):
            time.sleep(S2_RATE_LIMIT_DELAY)

    logger.info("Citation fetch complete. %d papers in cache.", len(cache))
    return cache


def build_citation_edges(cache, db_id_set):
    """Derive a deduplicated list of citation edges from the cache.

    Returns list of ``{"source": id1, "target": id2}`` dicts where
    *source* cites *target*.  Both endpoints must be in *db_id_set*.
    """
    seen = set()
    edges = []
    for paper_id, data in cache.items():
        if paper_id not in db_id_set:
            continue
        for ref_id in data.get("references", []):
            if ref_id in db_id_set:
                key = (paper_id, ref_id)
                if key not in seen:
                    seen.add(key)
                    edges.append({"source": paper_id, "target": ref_id})
    return edges
