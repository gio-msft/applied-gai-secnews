import os
import json
import logging

from pypdf import PdfReader
from pymongo import MongoClient
from secnews.utils_papers import download_paper, read_pages


logger = logging.getLogger("AIRT-GAI-SecNews")


def compress_content(metadata, llm_lingua, compressor_prompt: str) -> str:
    """Compress content using LLMLingua."""
    tmp = llm_lingua.compress_prompt(
        metadata["content"],
        instruction=compressor_prompt,
        rate=0.33,
        force_tokens=["\n", "?"],
        condition_in_question="after_condition",
        reorder_context="sort",
        dynamic_context_compression_ratio=0.3,  # or 0.4
        condition_compare=True,
        context_budget="+100",
        rank_method="longllmlingua",
    )
    logger.debug(
        "Compressed %s to %s (%s)"
        % (tmp["origin_tokens"], tmp["compressed_tokens"], tmp["rate"])
    )
    return tmp["compressed_prompt"]


def summarize_records(
    records: list,
    summarizer,
    summarizer_prompt: str,
    paper_path: str,
    research_db: MongoClient,
    compress_prompt: bool = False,
    llm_lingua=None,
) -> bool:
    """Use LLM to summarize paper content."""
    for record in records:
        logger.debug("Processing: %s" % record["id"])
        filename = paper_path + "%s.pdf" % (record["id"])
        try:
            reader = PdfReader(filename)
            # This call may fail with a FileNotFoundError if the file does not exist
            # It may also fail with other exceptions if the PDF is corrupted or unreadable
        except FileNotFoundError:
            download_paper(record["url"], research_db)
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
        oai_summarize = metadata["content"]
        if compress_prompt:
            oai_summarize = compress_content(metadata, llm_lingua, summarizer_prompt)
        results = summarizer.chat.completions.create(
            model=os.environ.get("AZURE_OPENAI_SUMMARY_MODEL_NAME"),
            messages=[
                {"role": "system", "content": summarizer_prompt},
                {"role": "user", "content": oai_summarize},
            ],
            response_format={"type": "json_object"},
        )
        loaded = json.loads(results.choices[0].message.content)
        logger.debug("Processed: %s" % record["id"])
        logger.debug(loaded)
        query = {"id": record["id"]}
        update = {
            "summarized": True,
            "points": loaded["findings"],
            "one_liner": loaded["one_liner"],
            "emoji": loaded["emoji"] if "emoji" in loaded else "🔍",
            "tag": loaded["tag"] if "tag" in loaded else "general",
        }
        setter = {"$set": update}
        research_db.update_one(query, setter)
    return True
