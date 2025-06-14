import os
import sys
import json
import dotenv
import urllib3
import logging
import datetime
import smtplib

from pathlib import Path
from pypdf import PdfReader
from openai import AzureOpenAI
from pymongo import MongoClient
from llmlingua import PromptCompressor
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from secnews.utils_search import execute_searches, assemble_feeds, prune_feeds
from secnews.utils_papers import download_papers, save, download_paper, assemble_records


# Load environment variables from .env file
# Ensure you have a .env file with AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY set
dotenv.load_dotenv(".env")

llm_lingua = PromptCompressor(
    model_name="microsoft/llmlingua-2-xlm-roberta-large-meetingbank",
    use_llmlingua2=True,
    device_map="cpu"
)

logger = logging.getLogger("AIRT-GAI-SecNews")
logger.setLevel(logging.DEBUG)
shandler = logging.StreamHandler(sys.stdout)
fmt = '\033[1;32m%(levelname)-5s %(module)s:%(funcName)s():'
fmt += '%(lineno)d %(asctime)s\033[0m| %(message)s'
shandler.setFormatter(logging.Formatter(fmt))
logger.addHandler(shandler)
urllib3.disable_warnings()

BASE_URL = 'https://export.arxiv.org/api/query'
BASE_OFFSET = 0
COMPRESS_PROMPT = False
EMAIL_LIST = []
MAX_RESULTS = 200
OAI = AzureOpenAI(
    azure_endpoint=os.environ.get("AZURE_OPENAI_ENDPOINT"),
    api_key=os.environ.get("AZURE_OPENAI_API_KEY"),
    api_version="2025-01-01-preview"
)
PROCESS_DAYS = 7
PAPER_PATH = "papers/"
SUMMARIES_PATH = "summaries/"
os.makedirs(PAPER_PATH, exist_ok=True)
SEARCHES = [
    {'search_query': 'all:"prompt%20injection"+AND+cat:cs.*', 'start': BASE_OFFSET, 'max_results': MAX_RESULTS},
    {'search_query': 'all:"jailbreak"+AND+"llm"+AND+cat:cs.*', 'start': BASE_OFFSET, 'max_results': MAX_RESULTS},
    {'search_query': 'all:"abuse"+AND+"llm"+AND+cat:cs.*', 'start': BASE_OFFSET, 'max_results': MAX_RESULTS},
    {'search_query': 'all:"attack"+AND+"llm"+AND+cat:cs.*', 'start': BASE_OFFSET, 'max_results': MAX_RESULTS},
    {'search_query': 'all:"vulnerability"+AND+"llm"+AND+cat:cs.*', 'start': BASE_OFFSET, 'max_results': MAX_RESULTS},
    {'search_query': 'all:"malware"+AND+"llm"+AND+cat:cs.*', 'start': BASE_OFFSET, 'max_results': MAX_RESULTS},
    {'search_query': 'all:"phishing"+AND+"llm"+AND+cat:cs.*', 'start': BASE_OFFSET, 'max_results': MAX_RESULTS},
    {'search_query': 'all:"hack"+AND+"llm"+AND+cat:cs.*', 'start': BASE_OFFSET, 'max_results': MAX_RESULTS},
    {'search_query': 'all:"hijack"+AND+"llm"+AND+cat:cs.*', 'start': BASE_OFFSET, 'max_results': MAX_RESULTS},
    {'search_query': 'all:"backdoor"+AND+"llm"+AND+cat:cs.*', 'start': BASE_OFFSET, 'max_results': MAX_RESULTS},
    {'search_query': 'all:"trojan"+AND+"llm"+AND+cat:cs.*', 'start': BASE_OFFSET, 'max_results': MAX_RESULTS},
    {'search_query': 'all:"exploit"+AND+"agent"+AND+cat:cs.*', 'start': BASE_OFFSET, 'max_results': MAX_RESULTS},
    {'search_query': 'all:"vulnerability"+AND+"agent"+AND+cat:cs.*', 'start': BASE_OFFSET, 'max_results': MAX_RESULTS},
    {'search_query': 'all:"hijack"+AND+"agent"+AND+cat:cs.*', 'start': BASE_OFFSET, 'max_results': MAX_RESULTS},
    {'search_query': 'all:"attack"+AND+"agent"+AND+cat:cs.*', 'start': BASE_OFFSET, 'max_results': MAX_RESULTS},
    {'search_query': 'all:"backdoor"+AND+"agent"+AND+cat:cs.*', 'start': BASE_OFFSET, 'max_results': MAX_RESULTS},
    {'search_query': 'all:"malware"+AND+"agent"+AND+cat:cs.*', 'start': BASE_OFFSET, 'max_results': MAX_RESULTS},
    {'search_query': 'all:"phishing"+AND+"agent"+AND+cat:cs.*', 'start': BASE_OFFSET, 'max_results': MAX_RESULTS},
    {'search_query': 'all:"hack"+AND+"agent"+AND+cat:cs.*', 'start': BASE_OFFSET, 'max_results': MAX_RESULTS},
    {'search_query': 'all:"trojan"+AND+"agent"+AND+cat:cs.*', 'start': BASE_OFFSET, 'max_results': MAX_RESULTS},
]
SENDER_EMAIL = os.environ.get("SENDER_EMAIL")
FROM_EMAIL = os.environ.get("FROM_EMAIL")
APP_PASSWORD = os.environ.get("APP_PASSWORD")
SYSTEM_PROMPT = """Assume the role of a technical writer. 
Present the main findings of the research succinctly. 
Summarize key findings by highlighting the most critical facts and actionable insights without directly referencing 'the research.'
Focus on outcomes, significant percentages or statistics, and their broader implications. 
Each point should stand on its own, conveying a clear fact or insight relevant to the field of study.

Format the output as a JSON object that follows the following template.

'findings' // array that contains 3 single-sentence findings.
'one_liner' // one-liner sentences noting what is interesting in the paper\
'emoji' // a single emoji that represents the paper
'tag' // a single word tag that represents the paper, this can either be 'security' for papers related to the security of ai systems, 'cyber' for papers related to using ai to help with cybersecurity task, and 'general' for all other items."""


def offset_existing_time_future(str_time, delta):
    """Return an offset datetime as a string."""
    existing = datetime.datetime.strptime(str_time, "%Y-%m-%d %H:%M:%S")
    offset = (existing + datetime.timedelta(days=delta))
    # return offset
    return offset.strftime("%Y-%m-%d %H:%M:%S")


def mongo_connect(host, port, database, collection):
    """Connect to local mongo instance."""
    return MongoClient(host, port)[database][collection]


RESEARCH_DB = mongo_connect('localhost', 27017, 'gaisecnews', 'papers')
TODAY = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
PULL_WINDOW = offset_existing_time_future(TODAY, -PROCESS_DAYS)




def read_pages(reader) -> dict:
    """Read all the pages from a loaded PDF."""
    tmp = {'pages': len(reader.pages), 'content': list(), 'characters': 0, 'tokens': 0}
    for i in range(len(reader.pages)):
        page = reader.pages[i]
        tmp['content'].append(page.extract_text())
    tmp['content'] = ' '.join(tmp['content'])
    tmp['characters'] = int(len(tmp['content']))
    tmp['tokens'] = int(round(len(tmp['content'])/4))
    return tmp


def compress_content(metadata):
    """Compress content using LLMLingua."""
    tmp = llm_lingua.compress_prompt(
        metadata['content'],
        instruction=SYSTEM_PROMPT,
        rate=0.33,
        force_tokens=['\n', '?'],
        condition_in_question="after_condition",
        reorder_context="sort",
        dynamic_context_compression_ratio=0.3, # or 0.4
        condition_compare=True,
        context_budget="+100",
        rank_method="longllmlingua",
    )
    logger.debug("Compressed %s to %s (%s)" % (tmp['origin_tokens'],
                                               tmp['compressed_tokens'],
                                               tmp['rate']))
    return tmp['compressed_prompt']


def summarize_records(records: list) -> bool:
    """Use LLM to summarize paper content."""
    for record in records:
        logger.debug("Processing: %s" % record['id'])
        filename = PAPER_PATH + "%s.pdf" % (record['id'])
        try:
            reader = PdfReader(filename)
            # This call may fail with a FileNotFoundError if the file does not exist
            # It may also fail with other exceptions if the PDF is corrupted or unreadable
        except FileNotFoundError:
            download_paper(record['url'], RESEARCH_DB)
            try:
                reader = PdfReader(filename)
            except Exception as e:
                #  Occurs when a paper URL is valid, yet the file is not.
                logger.error(e)
                continue
        except Exception as e:
            # Handle any other exceptions that may occur when reading the PDF
            logger.error(f"Error reading {filename}: {e}")
            continue
   
        metadata = read_pages(reader)
        oai_summarize = metadata['content']
        if COMPRESS_PROMPT:
            oai_summarize = compress_content(metadata)
        results = OAI.chat.completions.create(
            model=os.environ.get("AZURE_OPENAI_SUMMARY_MODEL_NAME"),
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": oai_summarize},
            ],
            response_format={"type": "json_object"}
        )
        loaded = json.loads(results.choices[0].message.content)
        logger.debug("Processed: %s" % record['id'])
        logger.debug(loaded)
        query = {'id': record['id']}
        update = {
            'summarized': True,
            'points': loaded['findings'],
            'one_liner': loaded['one_liner'],
            'emoji': loaded['emoji'] if 'emoji' in loaded else '🔍',
            'tag': loaded['tag'] if 'tag' in loaded else 'general'
        }
        setter = {'$set': update}
        RESEARCH_DB.update_one(query, setter)
    return True

def send_mail(subject, content, send_to):
    """Send mail out to users."""
    server = smtplib.SMTP('smtp.gmail.com', 587)
    server.starttls()
    server.login(SENDER_EMAIL, APP_PASSWORD)
    message = MIMEMultipart('alternative')
    message['subject'] = subject
    message['From'] = FROM_EMAIL
    message['To'] = send_to
    message.attach(MIMEText(content['plain'], 'plain'))
    message.attach(MIMEText(content['html'], 'html'))
    server.sendmail(message['From'], message['To'], message.as_string())
    server.quit()

def share_results() -> bool:
    """Prepare any result not yet shared and format."""
    query = {'$and': [
        {'published': {'$gte': PULL_WINDOW}},
        {'summarized': True},
        {'shared': False}
    ]}
    tmp = RESEARCH_DB.find(query)
    if not tmp:
        return False
    tmp = list(tmp)
    for record in tmp:
        logger.debug("%s %s" % (record['published'], record['title']))

    content = {'plain': '', 'html': '', 'markdown': ''}
    for record in tmp:
        content['plain'] += "%s %s\n %s - %s\n - %s\n" % (record['emoji'], record['title'], record['url'], record['tag'],  record['one_liner'])
        content['html'] += "<b>%s %s</b> (<a href='%s' target='_blank'>%s</a>)<br> %s - %s<br>" % (record['emoji'], record['title'], record['url'], record['url'], record['tag'], record['one_liner'])
        content['markdown'] += '%s **%s** [source](%s) #%s \n\n %s' % (record['emoji'], record['title'], record['url'], record['tag'], record['one_liner'])
        for point in record['points']:
            content['plain'] += "- %s\n" % (point)
            content['html'] += "<li>%s</li>" % (point)
            content['markdown'] += '\n - %s' % (point)
        content['plain'] += "\n\n"
        content['html'] += "<br>"
        content['markdown'] += "\n\n<br>\n\n"

    logger.debug(content)
    if len(content['plain']) > 0:
        for user in EMAIL_LIST:
            subject = "[%s] AIRT Gen AI Security News" % (TODAY[:10])
            send_mail(subject, content, user)

    for record in tmp:
        query = {'id': record['id']}
        setter = {'$set': {'shared': True}}
        RESEARCH_DB.update_one(query, setter)

    # Create a markdown file for sharing in SUMMARIES_PATH
    # The filename should be YYYY-MM-DD.md
    os.makedirs(SUMMARIES_PATH, exist_ok=True)
    markdown_file = Path(SUMMARIES_PATH + datetime.datetime.now().strftime('%Y-%m-%d') + '.md')
    with open(markdown_file, 'w') as f:
        f.write(content['markdown'])
    logger.info("Markdown file created: %s" % markdown_file)


    return True


if __name__ == "__main__":

    logger.info("[*] Executing searches...")
    feeds = execute_searches(BASE_URL, SEARCHES, list())
    logger.info("[*] Found %s feeds." % str(len(feeds)))

    logger.info("[*] Assembling feeds...")
    results = assemble_feeds(feeds, RESEARCH_DB)
    logger.info("[*] Deduplication - %s results." % str(len(results)))

    logger.info("[*] Pruning feeds...")
    valid = prune_feeds(results, PULL_WINDOW, PAPER_PATH)

    logger.info("[*] Downloading %s papers..." % str(len(valid)))
    download_papers(valid, RESEARCH_DB)

    logger.info("[*] Assembling records for summary...")
    records = assemble_records(PULL_WINDOW, RESEARCH_DB)
    logger.info("[*] Found %s records to summarize." % str(len(records)))

    # logger.info("[*] Summarizing %s papers..." % str(len(records)))
    # summarize_records(records)

    # logger.info("[*] Sharing summaries for %s papers..." % str(len(records)))
    # share_results()

    # logger.info("[$] FIN")
