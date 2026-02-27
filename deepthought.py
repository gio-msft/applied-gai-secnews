import os
import sys
import json
import dotenv
import logging
import datetime
import argparse

from openai import AzureOpenAI

from secnews.utils_db import PaperDB
from secnews.utils_comms import share_results
from secnews.utils_summary import summarize_records, classify_relevance, classify_project_relevance
from secnews.utils_papers import download_papers, assemble_records
from secnews.utils_search import execute_searches, assemble_feeds, prune_feeds


# Load environment variables from .env file
# Ensure you have a .env file with AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY set
dotenv.load_dotenv(".env")

logger = logging.getLogger("AIRT-GAI-SecNews")
logger.setLevel(logging.DEBUG)
shandler = logging.StreamHandler(sys.stdout)
fmt = "\033[1;32m%(levelname)-5s %(module)s:%(funcName)s():"
fmt += "%(lineno)d %(asctime)s\033[0m| %(message)s"
shandler.setFormatter(logging.Formatter(fmt))
logger.addHandler(shandler)

BASE_URL = "https://export.arxiv.org/api/query"
BASE_OFFSET = 0
DB_PATH = "papers.json"
MAX_RESULTS = 100
OAI = AzureOpenAI(
    azure_endpoint=os.environ.get("AZURE_OPENAI_ENDPOINT"),
    api_key=os.environ.get("AZURE_OPENAI_API_KEY"),
    api_version="2025-01-01-preview",
)
PROCESS_DAYS = 7
PAPER_PATH = "papers/"
SUMMARIES_PATH = "summaries/"

# arXiv category set for security-relevant CS papers
_CATS = "(cat:cs.CR+OR+cat:cs.CL+OR+cat:cs.AI+OR+cat:cs.LG)"

SEARCHES = [
    # --- LLM-specific security terms (precise) ---
    f'abs:"prompt+injection"+AND+{_CATS}',
    f'abs:"jailbreak"+AND+abs:"LLM"+AND+{_CATS}',
    f'abs:"jailbreak"+AND+abs:"language+model"+AND+{_CATS}',
    f'abs:"backdoor"+AND+abs:"LLM"+AND+{_CATS}',
    f'abs:"trojan"+AND+abs:"LLM"+AND+{_CATS}',
    f'abs:"malware"+AND+abs:"LLM"+AND+{_CATS}',
    f'abs:"phishing"+AND+abs:"LLM"+AND+{_CATS}',
    f'abs:"hijack"+AND+abs:"LLM"+AND+{_CATS}',
    f'abs:"attack"+AND+abs:"LLM"+AND+cat:cs.CR',
    f'abs:"vulnerability"+AND+abs:"LLM"+AND+cat:cs.CR',
    f'abs:"abuse"+AND+abs:"LLM"+AND+{_CATS}',
    # --- Agent security (require "LLM" or "language model" context) ---
    f'abs:"attack"+AND+abs:"LLM+agent"+AND+{_CATS}',
    f'abs:"hijack"+AND+abs:"agent"+AND+abs:"language+model"+AND+{_CATS}',
    f'abs:"backdoor"+AND+abs:"agent"+AND+abs:"language+model"+AND+{_CATS}',
    f'abs:"vulnerability"+AND+abs:"agent"+AND+abs:"language+model"+AND+{_CATS}',
    f'abs:"malware"+AND+abs:"agent"+AND+abs:"language+model"+AND+{_CATS}',
    f'abs:"phishing"+AND+abs:"agent"+AND+abs:"language+model"+AND+{_CATS}',
    f'abs:"trojan"+AND+abs:"agent"+AND+abs:"language+model"+AND+{_CATS}',
    # --- Additional high-value queries ---
    f'abs:"red+teaming"+AND+abs:"LLM"+AND+{_CATS}',
    f'abs:"adversarial"+AND+abs:"language+model"+AND+cat:cs.CR',
    f'abs:"data+poisoning"+AND+abs:"LLM"+AND+{_CATS}',
    f'abs:"safety+alignment"+AND+abs:"LLM"+AND+{_CATS}',
    f'abs:"agentic"+AND+abs:"security"+AND+{_CATS}',
    f'abs:"MCP"+AND+abs:"security"+AND+{_CATS}',
]
SEARCHES = [
    {"search_query": search, "start": BASE_OFFSET, "max_results": MAX_RESULTS}
    for search in SEARCHES
]

SYSTEM_PROMPT = """Assume the role of an expert AI Security and Safety researcher and technical writer. 
Present the main findings of the research succinctly. 
Summarize key findings by highlighting the most critical facts and actionable insights without directly referencing 'the research.'
Focus on outcomes, significant percentages or statistics, and their broader implications. 
Each point should stand on its own, conveying a clear fact or insight relevant to the field of study.

Format the output as a JSON object that follows the following template.

'findings' // array that contains 3 single-sentence findings.
'one_liner' // one-liner sentences noting what is interesting in the paper
'emoji' // a single emoji that represents the paper
'tag' // a single word tag classifying the paper's PRIMARY topic. Use strict definitions:
  - 'security': the paper is DIRECTLY about attacking, defending, or evaluating the security, safety, or privacy OF AI/LLM/agent systems themselves (e.g. jailbreaking LLMs, prompt injection, backdoors in models, adversarial attacks on LLMs, LLM alignment, AI privacy like differential privacy for RAG/LLMs, watermarking LLM outputs, agent hijacking).
  - 'cyber': the paper uses AI/LLMs as a TOOL to perform cybersecurity tasks (e.g. AI-powered penetration testing, LLM-based vulnerability detection, AI for malware analysis, AI for incident response).
  - 'general': EVERYTHING ELSE. This includes papers about traditional security topics (network security, cryptographic protocols, formal verification, smart contracts, wireless security, zero-knowledge proofs) even if they mention or use AI. Also includes general ML/AI papers that are not about security/safety/privacy of AI systems.
  When in doubt between 'security' and 'general', ask: 'Is this paper primarily about a threat to, defense of, or privacy property of an AI/LLM/agent system?' If no, use 'general'.
'affiliations' // array of unique institutional affiliations of the authors, extracted from the paper text (e.g. ["MIT", "Google DeepMind", "Stanford University"]). If not found, return an empty array."""

RELEVANCE_PROMPT = """You are a topic classifier for a newsletter about Generative AI security research.

Given a paper's title and summary, determine if it is RELEVANT to the newsletter.

RELEVANT papers are about:
- Attacking, defending, or evaluating the security/safety/privacy OF AI/LLM/agent systems (e.g. jailbreaking, prompt injection, backdoors, adversarial attacks, alignment, AI privacy, watermarking, agent hijacking)
- Using AI/LLMs as tools FOR cybersecurity tasks (e.g. AI-powered pentesting, LLM-based vulnerability detection, AI for malware analysis)

NOT RELEVANT:
- Traditional security (network security, cryptographic protocols, formal verification, wireless security, zero-knowledge proofs) even if AI is mentioned
- Smart contracts, blockchain security
- General ML/AI papers not about security/safety/privacy of AI systems

Respond with a JSON object: {"relevant": true} or {"relevant": false}"""

PROJECT_RELEVANCE_PROMPT = """You are a research project matcher.

Given a paper's title and summary, determine if it is relevant to any of the following research projects.
A paper is relevant to a project if its topic, methods, or findings could directly inform or advance that project.

Projects:
{projects}

Respond with a JSON object: {{"projects": ["project-id-1", "project-id-2"]}} containing the IDs of matching projects.
If the paper is not relevant to any project, respond with: {{"projects": []}}"""

# Load research project definitions for project-relevance classification
PROJECTS_PATH = "projects.json"
try:
    with open(PROJECTS_PATH) as _f:
        PROJECTS = json.load(_f)
except (FileNotFoundError, json.JSONDecodeError):
    PROJECTS = []


def make_pull_window(process_days):
    """Return pull_window as an ISO 8601 string."""
    now = datetime.datetime.now(datetime.timezone.utc)
    window = now - datetime.timedelta(days=process_days)
    return window.strftime("%Y-%m-%dT%H:%M:%SZ")


if __name__ == "__main__":

    parser = argparse.ArgumentParser(description="AIRT GAI Security News Pipeline")
    parser.add_argument(
        "--force-search", action="store_true",
        help="Ignore search cache and re-execute all arXiv searches",
    )
    parser.add_argument(
        "--resummarize", action="store_true",
        help="Re-summarize papers in the window (skip search/download)",
    )
    parser.add_argument(
        "--include-general", action="store_true",
        help="Include all papers in the output regardless of relevance",
    )
    parser.add_argument(
        "--no-interactive", action="store_true",
        help="Skip interactive review of borderline papers",
    )
    parser.add_argument(
        "--share-only", action="store_true",
        help="Skip all processing, just regenerate output files from existing data",
    )
    parser.add_argument(
        "--reclassify-projects", action="store_true",
        help="Re-run project classification on relevant papers in the window",
    )
    args = parser.parse_args()

    logger.info("[$] Starting AIRT-GAI-SecNews...")
    os.makedirs(PAPER_PATH, exist_ok=True)
    paper_db = PaperDB(DB_PATH)
    pull_window = make_pull_window(PROCESS_DAYS)

    if args.share_only:
        logger.info("[*] Share-only mode, skipping search/download/summarize...")
    elif args.resummarize:
        count = paper_db.reset_summarized(pull_window)
        logger.info("[*] Reset %d papers for re-summarization." % count)
    else:
        logger.info("[*] Executing searches...")
        feeds = execute_searches(base=BASE_URL, params=SEARCHES, force=args.force_search)
        logger.info("[*] Found %s feeds." % str(len(feeds)))

        logger.info("[*] Assembling feeds...")
        results = assemble_feeds(feeds=feeds, paper_db=paper_db)
        logger.info("[*] Deduplication - %s results." % str(len(results)))

        logger.info("[*] Pruning feeds...")
        valid = prune_feeds(feeds=results, pull_window=pull_window, paper_path=PAPER_PATH)

        logger.info("[*] Downloading %s papers..." % str(len(valid)))
        download_papers(results=valid, paper_db=paper_db, paper_path=PAPER_PATH)

    if not args.share_only:
        logger.info("[*] Assembling records for summary...")
        records = assemble_records(pull_window=pull_window, paper_db=paper_db)
        logger.info("[*] Found %s records to summarize." % str(len(records)))

        logger.info("[*] Summarizing %s papers..." % str(len(records)))
        summarize_records(
            records=records,
            summarizer=OAI,
            summarizer_prompt=SYSTEM_PROMPT,
            paper_path=PAPER_PATH,
            paper_db=paper_db,
        )

        logger.info("[*] Classifying relevance...")
        all_in_window = paper_db.find(published_gte=pull_window, summarized=True)
        classify_relevance(
            records=all_in_window,
            classifier=OAI,
            relevance_prompt=RELEVANCE_PROMPT,
            paper_db=paper_db,
        )

        # Interactive review: show papers tagged security/cyber but marked irrelevant
        if not args.no_interactive and not args.include_general:
            borderline = [
                r for r in paper_db.find(published_gte=pull_window, summarized=True)
                if r.get("tag") in ("security", "cyber") and r.get("relevant") is False
            ]
            if borderline:
                print("\n" + "=" * 70)
                print("The following papers were tagged security/cyber but classified")
                print("as NOT directly about AI security. Review and include any?")
                print("=" * 70)
                for i, r in enumerate(borderline, 1):
                    print(f"\n  [{i}] {r['title']}")
                    print(f"      {r.get('one_liner', '')}")
                print()
                try:
                    choice = input("Enter numbers to include (comma-separated), or Enter to skip: ").strip()
                    if choice:
                        indices = [int(x.strip()) - 1 for x in choice.split(",") if x.strip().isdigit()]
                        for idx in indices:
                            if 0 <= idx < len(borderline):
                                paper_db.update(borderline[idx]["id"], {"relevant": True})
                                logger.info("Included: %s" % borderline[idx]["title"])
                except (EOFError, KeyboardInterrupt):
                    pass  # non-interactive environment, skip

        # Project relevance classification
        if PROJECTS:
            if args.reclassify_projects:
                for r in paper_db.find(published_gte=pull_window, summarized=True):
                    if "projects" in r:
                        paper_db.update(r["id"], {"projects": None})
                        # Remove the key by re-reading (update merges, so set None
                        # then pop). Simpler: just delete from the record dict.
                # Re-fetch after clearing
                for r in paper_db.find(published_gte=pull_window, summarized=True):
                    r.pop("projects", None)
                paper_db._save()
                logger.info("[*] Cleared project classifications for re-run.")

            relevant_papers = [
                r for r in paper_db.find(published_gte=pull_window, summarized=True)
                if r.get("relevant") is True
            ]
            project_list_str = "\n".join(
                f"- {p['id']}: {p['description']}" for p in PROJECTS
            )
            project_prompt = PROJECT_RELEVANCE_PROMPT.format(projects=project_list_str)
            project_ids = [p["id"] for p in PROJECTS]
            logger.info("[*] Classifying project relevance for %d papers..." % len(relevant_papers))
            classify_project_relevance(
                records=relevant_papers,
                classifier=OAI,
                prompt=project_prompt,
                project_ids=project_ids,
                paper_db=paper_db,
            )
        else:
            logger.info("[*] No projects defined, skipping project classification.")

    logger.info("[*] Sharing summaries...")
    share_results(
        pull_window=pull_window,
        paper_db=paper_db,
        summaries_path=SUMMARIES_PATH,
        include_all=args.include_general,
    )

    logger.info("[$] FIN")
