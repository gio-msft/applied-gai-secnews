import os
import logging

from concurrent.futures import wait
from requests_futures.sessions import FuturesSession

logger = logging.getLogger("AIRT-GAI-SecNews")

USER_AGENT = "Mozilla/5.0 (Windows NT 6.3; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/33.0.1750.117 Safari/537.36"


def _request_bulk(urls):
    """Batch the requests going out."""
    if not urls:
        return []
    session = FuturesSession()
    futures = [
        session.get(u, headers={"User-Agent": USER_AGENT}, timeout=20) for u in urls
    ]
    done, _ = wait(futures)
    results = []
    for response in done:
        try:
            results.append(response.result())
        except Exception as err:
            logger.error("Failed result: %s" % err)
    return results


def _filename_from_url(url):
    """Extract filename from a URL, ensuring a single .pdf extension."""
    name = url.split("/")[-1]
    if not name.endswith(".pdf"):
        name += ".pdf"
    return name


def _save(paper_path, filename, content):
    """Save binary content to paper_path/filename."""
    filepath = os.path.join(paper_path, filename)
    with open(filepath, "wb") as f:
        f.write(content)


def download_papers(results: list, paper_db, paper_path: str) -> bool:
    """Download all papers and save them locally."""
    urls = list({r["url"] for r in results})
    responses = _request_bulk(urls)
    for item in responses:
        filename = _filename_from_url(item.url)
        _save(paper_path, filename, item.content)
        paper_id = filename.replace(".pdf", "")
        paper_db.update(paper_id, {"downloaded": True})
    return True


def download_paper(url, paper_db, paper_path: str) -> bool:
    """Download a single paper and save it locally."""
    logger.debug("Downloading: %s" % url)
    responses = _request_bulk([url])
    for item in responses:
        filename = _filename_from_url(item.url)
        _save(paper_path, filename, item.content)
        paper_id = filename.replace(".pdf", "")
        paper_db.update(paper_id, {"downloaded": True})
    return True


def assemble_records(pull_window: str, paper_db) -> list:
    """Gather any records in process window not yet summarized."""
    return paper_db.find(published_gte=pull_window, summarized=False)


def read_pages(reader) -> dict:
    """Read all the pages from a loaded PDF."""
    pages = []
    for page in reader.pages:
        pages.append(page.extract_text())
    content = " ".join(pages)
    return {
        "pages": len(reader.pages),
        "content": content,
        "characters": len(content),
    }
