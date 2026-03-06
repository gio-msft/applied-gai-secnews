import json
import time
import random
import urllib
import logging
import datetime
import requests
import feedparser

from pathlib import Path

logger = logging.getLogger("AIRT-GAI-SecNews")


def _load_search_state(state_path):
    """Load the search state file (maps query -> last completion timestamp)."""
    path = Path(state_path)
    if path.exists():
        return json.loads(path.read_text())
    return {}


def _save_search_state(state, state_path):
    """Persist search state to disk."""
    Path(state_path).write_text(json.dumps(state, indent=2))


def execute_searches(
    base, params: list, state_path="search_state.json", cache_hours=1, force=False
) -> list:
    """Collect all results from searches with pagination.

    Tracks completed searches in a state file. Skips any search that was
    successfully completed within the last ``cache_hours`` hours unless
    ``force=True``.  State is saved after each query completes, so the
    process can be interrupted and restarted without re-doing finished work.
    """
    logger.info("Number of searches: %d", len(params))
    state = _load_search_state(state_path)
    now = datetime.datetime.now(datetime.timezone.utc)
    cutoff = (now - datetime.timedelta(hours=cache_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")

    results = []
    for i, item in enumerate(params, 1):
        search_query = item["search_query"]

        # Skip if this search was recently completed (and not forced)
        if not force and search_query in state and state[search_query] >= cutoff:
            logger.info(
                "[%d/%d] Skipping (cached): %s", i, len(params), search_query
            )
            continue

        current_start = item["start"]
        max_results_per_request = item["max_results"]
        logger.info("[%d/%d] Searching: %s", i, len(params), search_query)

        retry_count = 0
        max_retries = 5
        backoff_base = 15  # seconds
        
        while True:
            time.sleep(random.uniform(2.0, 4.0))

            current_params = {
                "search_query": search_query,
                "start": current_start,
                "max_results": max_results_per_request,
            }

            payload_str = urllib.parse.urlencode(current_params, safe=":+")
            logger.debug("Executing search with params: %s", payload_str)

            response = requests.get(base, params=payload_str)
            feed = feedparser.parse(response.content)

            # Check if the API returned a valid response with OpenSearch metadata
            if not hasattr(feed.feed, 'opensearch_totalresults'):
                logger.warning(
                    "API response missing opensearch_totalresults (likely rate limited or error)"
                )
                
                # Log detailed error information
                logger.debug("Response status: %d", response.status_code)
                logger.debug("Feed keys: %s", list(feed.feed.keys()) if hasattr(feed, 'feed') else 'none')
                
                # Check for error entries in the feed
                if hasattr(feed.feed, 'title'):
                    logger.error("Feed title: %s", feed.feed.title)
                if hasattr(feed.feed, 'subtitle'):
                    logger.error("Feed subtitle: %s", feed.feed.subtitle)
                if feed.entries:
                    logger.error("Feed has %d entries despite missing opensearch data", len(feed.entries))
                else:
                    logger.error("Feed has no entries - likely malformed query or API rejection")
                    # Log first 500 chars of response for debugging
                    logger.debug("Response preview: %s", response.text[:500])
                
                # Retry with exponential backoff + jitter
                if retry_count < max_retries:
                    retry_count += 1
                    
                    # Use longer backoff for rate limit errors (429)
                    if response.status_code == 429:
                        base_wait = backoff_base * 2 * (2 ** (retry_count - 1))  # Double the backoff for 429
                        logger.warning("Rate limit (429) detected - using extended backoff")
                    else:
                        base_wait = backoff_base * (2 ** (retry_count - 1))
                    
                    # Add random jitter between 0.5x and 1.5x the base wait time
                    jitter = random.uniform(0.5, 1.5)
                    wait_time = int(base_wait * jitter)
                    logger.info(
                        "Retry %d/%d after %d seconds...",
                        retry_count, max_retries, wait_time
                    )
                    time.sleep(wait_time)
                    continue
                else:
                    logger.error(
                        "Max retries exceeded for query: %s at start=%d",
                        search_query, current_start
                    )
                    break  # Skip to next search query

            # Valid response, reset retry counter
            retry_count = 0
            
            total_results = int(feed.feed.opensearch_totalresults)
            start_index = int(feed.feed.opensearch_startindex)
            returned_results = len(feed.entries)

            logger.debug(
                "Total: %d | Start: %d | Batch: %d",
                total_results, start_index, returned_results,
            )

            results.append(response.content)

            break1 = returned_results < max_results_per_request
            break2 = (start_index + returned_results) >= total_results
            if break1 or break2:
                logger.debug("Completed search for: %s", search_query)
                break

            current_start = start_index + returned_results
            logger.debug("Fetching next page starting at: %d", current_start)

        # Persist immediately so interrupted runs can resume here
        state[search_query] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        _save_search_state(state, state_path)

    return results


# Feed processing functions


def process_feed(response, paper_db) -> list:
    """Process feed into list, inserting new papers into the database."""
    results = []
    feed = feedparser.parse(response)
    for entry in feed.entries:
        url = "%s.pdf" % entry.id.replace("abs", "pdf")
        authors = [a.get("name", "") for a in getattr(entry, "authors", [])]
        obj = {
            "id": entry.id.split("/abs/")[-1],
            "url": url,
            "published": entry.published,
            "title": entry.title,
            "authors": authors,
            "downloaded": False,
            "summarized": False,
        }
        results.append(obj)
        if not paper_db.has_url(obj["url"]):
            paper_db.insert(obj)
    return results


def assemble_feeds(feeds: list, paper_db) -> list:
    """Process all the feeds into a deduplicated list."""
    results = []
    for feed in feeds:
        results += process_feed(feed, paper_db)
    seen = set()
    deduplicated = []
    for item in results:
        if item["url"] not in seen:
            seen.add(item["url"])
            deduplicated.append(item)
    return deduplicated


def prune_feeds(feeds: list, pull_window: str, paper_path: str) -> list:
    """Prune the list of feeds to only those within the time window and not yet downloaded."""
    valid = []
    for feed in feeds:
        # published is already normalized to ISO 8601 by PaperDB.insert()
        # but in-memory feed objects still have the raw arxiv date
        published = _normalize_iso(feed["published"])
        if published < pull_window:
            continue
        filename = Path(paper_path) / (feed["id"] + ".pdf")
        if filename.is_file():
            continue
        valid.append(feed)
    return valid


def _normalize_iso(iso_str):
    """Normalize an ISO 8601 date to a consistent UTC format for comparison."""
    iso_str = iso_str.replace("Z", "+00:00")
    dt = datetime.datetime.fromisoformat(iso_str)
    dt_utc = dt.astimezone(datetime.timezone.utc)
    return dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
