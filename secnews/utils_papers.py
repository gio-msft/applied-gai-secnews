import random
import logging

from pymongo import MongoClient
from concurrent.futures import wait
from requests_futures.sessions import FuturesSession

logger = logging.getLogger("AIRT-GAI-SecNews")


def gen_headers():
    """Generate a header pairing."""
    ua_list = [
        "Mozilla/5.0 (Windows NT 6.3; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/33.0.1750.117 Safari/537.36"
    ]
    headers = {"User-Agent": ua_list[random.randint(0, len(ua_list) - 1)]}
    return headers


def _request_bulk(urls):
    """Batch the requests going out."""
    if not urls:
        return list()
    session: FuturesSession = FuturesSession()
    futures = [
        session.get(u, headers=gen_headers(), timeout=20, verify=False) for u in urls
    ]
    done, _ = wait(futures)
    results = list()
    for response in done:
        try:
            tmp = response.result()
            results.append(tmp)
        except Exception as err:
            logger.error("Failed result: %s" % err)
    return results


def save(id, content):
    """Save the file information."""
    f = open("papers/%s" % id, "wb")
    f.write(content)
    f.close()


def download_papers(results: list, research_db: MongoClient) -> bool:
    """Download all papers and save them locally."""
    urls = list()
    for result in results:
        urls.append(result["url"])
    urls = list(set(urls))
    responses = _request_bulk(urls)
    for item in responses:
        filename = item.url.split("/")[-1] + ".pdf"
        save(filename, item.content)
        query = {"id": filename.replace(".pdf", "")}
        setter = {"$set": {"downloaded": True}}
        research_db.update_one(query, setter)
    return True


def download_paper(url, research_db: MongoClient) -> bool:
    """Download all papers and save them locally."""
    logger.debug("Downloading: %s" % url)
    responses = _request_bulk([url])
    for item in responses:
        filename = item.url.split("/")[-1] + ".pdf"
        save(filename, item.content)
        query = {"id": filename.replace(".pdf", "")}
        setter = {"$set": {"downloaded": True}}
        research_db.update_one(query, setter)
    return True



def assemble_records(pull_window: str, research_db: MongoClient) -> list:
    """Gather any records in process window not yet summarized."""
    query = {'$and': [
        {'published': {'$gte': pull_window}},
        {'summarized': False}
    ]}
    tmp = research_db.find(query)
    if not tmp:
        tmp = list()
    return list(tmp)