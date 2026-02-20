import os
import sys
import dotenv
import urllib3
import logging
import datetime

from openai import AzureOpenAI
from pymongo import MongoClient

# from llmlingua import PromptCompressor  # Deprecated import

from secnews.utils_comms import share_results
from secnews.utils_summary import summarize_records
from secnews.utils_papers import download_papers, assemble_records
from secnews.utils_search import execute_searches, assemble_feeds, prune_feeds


# Load environment variables from .env file
# Ensure you have a .env file with AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY set
dotenv.load_dotenv(".env")

# Deprecated
# llm_lingua = PromptCompressor(
#     model_name="microsoft/llmlingua-2-xlm-roberta-large-meetingbank",
#     use_llmlingua2=True,
#     device_map="cpu",
# )

logger = logging.getLogger("AIRT-GAI-SecNews")
logger.setLevel(logging.DEBUG)
shandler = logging.StreamHandler(sys.stdout)
fmt = "\033[1;32m%(levelname)-5s %(module)s:%(funcName)s():"
fmt += "%(lineno)d %(asctime)s\033[0m| %(message)s"
shandler.setFormatter(logging.Formatter(fmt))
logger.addHandler(shandler)
urllib3.disable_warnings()

BASE_URL = "https://export.arxiv.org/api/query"
BASE_OFFSET = 0
COMPRESS_PROMPT = False
EMAIL_LIST = []
MAX_RESULTS = 200
OAI = AzureOpenAI(
    azure_endpoint=os.environ.get("AZURE_OPENAI_ENDPOINT"),
    api_key=os.environ.get("AZURE_OPENAI_API_KEY"),
    api_version="2025-01-01-preview",
)
PROCESS_DAYS = 7
PAPER_PATH = "papers/"
SUMMARIES_PATH = "summaries/"
SEARCHES = [
    'all:"prompt%20injection"+AND+cat:cs.*',
    'all:"jailbreak"+AND+"llm"+AND+cat:cs.*',
    'all:"abuse"+AND+"llm"+AND+cat:cs.*',
    'all:"attack"+AND+"llm"+AND+cat:cs.*',
    'all:"vulnerability"+AND+"llm"+AND+cat:cs.*',
    'all:"malware"+AND+"llm"+AND+cat:cs.*',
    'all:"phishing"+AND+"llm"+AND+cat:cs.*',
    'all:"hack"+AND+"llm"+AND+cat:cs.*',
    'all:"hijack"+AND+"llm"+AND+cat:cs.*',
    'all:"backdoor"+AND+"llm"+AND+cat:cs.*',
    'all:"trojan"+AND+"llm"+AND+cat:cs.*',
    'all:"exploit"+AND+"agent"+AND+cat:cs.*',
    'all:"vulnerability"+AND+"agent"+AND+cat:cs.*',
    'all:"hijack"+AND+"agent"+AND+cat:cs.*',
    'all:"attack"+AND+"agent"+AND+cat:cs.*',
    'all:"backdoor"+AND+"agent"+AND+cat:cs.*',
    'all:"malware"+AND+"agent"+AND+cat:cs.*',
    'all:"phishing"+AND+"agent"+AND+cat:cs.*',
    'all:"hack"+AND+"agent"+AND+cat:cs.*',
    'all:"trojan"+AND+"agent"+AND+cat:cs.*',
]
SEARCHES = [
    {"search_query": search, "start": BASE_OFFSET, "max_results": MAX_RESULTS}
    for search in SEARCHES
]

SYSTEM_PROMPT = """Assume the role of a technical writer. 
Present the main findings of the research succinctly. 
Summarize key findings by highlighting the most critical facts and actionable insights without directly referencing 'the research.'
Focus on outcomes, significant percentages or statistics, and their broader implications. 
Each point should stand on its own, conveying a clear fact or insight relevant to the field of study.

Format the output as a JSON object that follows the following template.

'findings' // array that contains 3 single-sentence findings.
'one_liner' // one-liner sentences noting what is interesting in the paper
'emoji' // a single emoji that represents the paper
'tag' // a single word tag that represents the paper, this can either be 'security' for papers related to the security of ai systems, 'cyber' for papers related to using ai to help with cybersecurity task, and 'general' for all other items."""


def offset_existing_time_future(str_time, delta):
    """Return an offset datetime as a string."""
    existing = datetime.datetime.strptime(str_time, "%Y-%m-%d %H:%M:%S")
    offset = existing + datetime.timedelta(days=delta)
    return offset.strftime("%Y-%m-%d %H:%M:%S")


def mongo_connect(host, port, database, collection):
    """Connect to local mongo instance."""
    return MongoClient(host, port)[database][collection]


if __name__ == "__main__":

    logger.info("[$] Starting AIRT-GAI-SecNews...")
    os.makedirs(PAPER_PATH, exist_ok=True)
    research_db = mongo_connect("localhost", 27017, "gaisecnews", "papers")
    today = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    pull_window = offset_existing_time_future(today, -PROCESS_DAYS)

    logger.info("[*] Executing searches...")
    feeds = execute_searches(base=BASE_URL, params=SEARCHES, results=list())
    logger.info("[*] Found %s feeds." % str(len(feeds)))

    logger.info("[*] Assembling feeds...")
    results = assemble_feeds(feeds=feeds, research_db=research_db)
    logger.info("[*] Deduplication - %s results." % str(len(results)))

    logger.info("[*] Pruning feeds...")
    valid = prune_feeds(feeds=results, pull_window=pull_window, paper_path=PAPER_PATH)

    logger.info("[*] Downloading %s papers..." % str(len(valid)))
    download_papers(results=valid, research_db=research_db)

    logger.info("[*] Assembling records for summary...")
    records = assemble_records(pull_window=pull_window, research_db=research_db)
    logger.info("[*] Found %s records to summarize." % str(len(records)))

    logger.info("[*] Summarizing %s papers..." % str(len(records)))
    summarize_records(
        records=records,
        summarizer=OAI,
        summarizer_prompt=SYSTEM_PROMPT,
        paper_path=PAPER_PATH,
        research_db=research_db,
        compress_prompt=False,
        llm_lingua=None,
    )
    logger.info("[*] Sharing summaries for %s papers..." % str(len(records)))
    share_results(
        pull_window=pull_window,
        research_db=research_db,
        email_list=EMAIL_LIST,
        today=today,
        summaries_path=SUMMARIES_PATH,
    )

    logger.info("[$] FIN")
