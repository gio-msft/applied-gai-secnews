import time
import random
import urllib
import datetime
import requests
import datetime
import feedparser

from pathlib import Path
from pymongo import MongoClient


def execute_searches(base, params: list, results: list) -> list:
    print(f"Number of searches: {len(params)}")
    """Recursively collect all results from searches."""
    for item in params:
        # Collect all pages for this search query
        current_start = item["start"]
        search_query = item["search_query"]
        max_results_per_request = item["max_results"]

        while True:
            # Random delay to avoid hitting rate limits
            time.sleep(random.uniform(0.5, 1.5))

            # Create current request parameters
            current_params = {
                "search_query": search_query,
                "start": current_start,
                "max_results": max_results_per_request,
            }

            payload_str = urllib.parse.urlencode(current_params, safe=":+")
            print(f"Executing search with params: {payload_str}")

            response = requests.get(base, params=payload_str)
            feed = feedparser.parse(response.content)

            total_results = int(feed.feed.opensearch_totalresults)
            start_index = int(feed.feed.opensearch_startindex)
            returned_results = len(feed.entries)

            print(f"Total results available: {total_results}")
            print(f"Start index: {start_index}")
            print(f"Results in this batch: {returned_results}")

            # Add this batch to results
            results.append(response.content)

            # Check if we need to fetch more pages
            # If we got fewer results than requested, or if we've reached the end
            break1 = returned_results < max_results_per_request
            break2 = (start_index + returned_results) >= total_results
            if break1 or break2:
                print(f"Completed search for: {search_query}")
                break

            # Prepare for next page
            current_start = start_index + returned_results
            print(f"Fetching next page starting at: {current_start}")

    return results


# Feed processing functions


def process_feed(response, research_db: MongoClient) -> list:
    """Process feed into list."""
    results = list()
    feed = feedparser.parse(response)
    for entry in feed.entries:
        url = "%s.pdf" % entry.id.replace("abs", "pdf")
        obj = {
            "id": entry.id.split("/abs/")[-1],
            "url": url,
            "published": entry.published,
            "title": entry.title,
            "downloaded": False,
            "summarized": False,
            "shared": False,
        }
        results.append(obj)
        query = {"url": obj["url"]}
        if research_db.count_documents(query) <= 0:
            research_db.insert_one(obj)
    return results


def assemble_feeds(feeds: list, research_db: MongoClient) -> list:
    """Process all the feeds into a deduplicated list."""
    results = list()
    for feed in feeds:
        results += process_feed(feed, research_db)
    seen = set()
    deduplicated = [
        x for x in results if [(x["url"]) not in seen, seen.add((x["url"]))][0]
    ]
    return deduplicated


def isodate_formatted(iso):
    """Return a formatted date from an ISO."""
    tmp = datetime.datetime.fromisoformat(iso[:-1] + "+00:00")
    return tmp.strftime("%Y-%m-%d %H:%M:%S")


def prune_feeds(feeds: list, pull_window: str, paper_path: str) -> list:
    """Prune the list of feeds to only those that match our criteria."""
    valid = list()
    for feed in feeds:
        published = isodate_formatted(feed["published"])
        if published < pull_window:
            continue
        filename = Path(paper_path + feed["id"] + ".pdf")
        if filename.is_file():
            continue
        valid.append(feed)
    return valid
