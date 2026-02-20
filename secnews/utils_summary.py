import os
import json
import time
import logging

from pypdf import PdfReader
from secnews.utils_papers import download_paper, read_pages


logger = logging.getLogger("AIRT-GAI-SecNews")


def _validate_affiliations(affiliations, arxiv_authors, pdf_text, paper_id):
    """Cross-check LLM-extracted affiliations against arXiv author metadata.

    Verifies that at least some arXiv author last names appear in the PDF text
    (confirming the PDF matches the metadata). If the authors don't match,
    the affiliations are likely hallucinated and are discarded.
    """
    # Extract last names from arXiv metadata for fuzzy matching
    arxiv_last_names = {name.split()[-1].lower() for name in arxiv_authors if name}
    if not arxiv_last_names:
        # No arXiv authors to validate against — keep affiliations as-is
        return affiliations
    pdf_lower = pdf_text[:3000].lower()  # check first ~3000 chars (title page)

    # Count how many arXiv author last names appear in the PDF title page
    matched = sum(1 for name in arxiv_last_names if name in pdf_lower)
    match_ratio = matched / len(arxiv_last_names) if arxiv_last_names else 0

    if match_ratio < 0.5:
        logger.warning(
            "Author mismatch for %s: only %d/%d arXiv authors found in PDF. "
            "Discarding affiliations.",
            paper_id, matched, len(arxiv_last_names),
        )
        return []

    return affiliations


def summarize_records(
    records: list,
    summarizer,
    summarizer_prompt: str,
    paper_path: str,
    paper_db,
) -> bool:
    """Use LLM to summarize paper content."""
    for record in records:
        time.sleep(3)  # To avoid rate limiting
        logger.debug("Processing: %s" % record["id"])
        filename = os.path.join(paper_path, "%s.pdf" % record["id"])
        try:
            reader = PdfReader(filename)
        except FileNotFoundError:
            download_paper(record["url"], paper_db, paper_path)
            try:
                reader = PdfReader(filename)
            except Exception as e:
                logger.error(e)
                continue
        except Exception as e:
            logger.error(f"Error reading {filename}: {e}")
            continue

        metadata = read_pages(reader)
        try:
            results = summarizer.chat.completions.create(
                model=os.environ.get("AZURE_OPENAI_SUMMARY_MODEL_NAME"),
                messages=[
                    {"role": "system", "content": summarizer_prompt},
                    {"role": "user", "content": metadata["content"]},
                ],
                response_format={"type": "json_object"},
            )
            loaded = json.loads(results.choices[0].message.content)
            logger.debug("Processed: %s" % record["id"])
            logger.debug(loaded)

            affiliations = loaded.get("affiliations", [])
            # Validate: only keep affiliations if the LLM-extracted authors
            # overlap with the arXiv metadata authors (guard against hallucination)
            arxiv_authors = record.get("authors", [])
            if affiliations and arxiv_authors:
                affiliations = _validate_affiliations(
                    affiliations, arxiv_authors, metadata["content"], record["id"]
                )

            paper_db.update(record["id"], {
                "summarized": True,
                "points": loaded["findings"],
                "one_liner": loaded["one_liner"],
                "emoji": loaded.get("emoji", "\U0001f50d"),
                "tag": loaded.get("tag", "general"),
                "affiliations": affiliations,
            })
        except Exception as e:
            logger.error(f"Error summarizing {record['id']}: {e}")
            continue
    return True


def classify_relevance(records, classifier, relevance_prompt, paper_db):
    """Classify summarized records as relevant/irrelevant using a second LLM call.

    Only classifies records tagged 'security' or 'cyber' that haven't been
    classified yet. Papers tagged 'general' are auto-marked irrelevant.
    """
    for record in records:
        if "relevant" in record:
            continue  # already classified

        tag = record.get("tag", "general")
        if tag == "general":
            paper_db.update(record["id"], {"relevant": False})
            continue

        title = record.get("title", "")
        one_liner = record.get("one_liner", "")
        prompt_text = f"Title: {title}\nSummary: {one_liner}"

        try:
            result = classifier.chat.completions.create(
                model=os.environ.get("AZURE_OPENAI_SUMMARY_MODEL_NAME"),
                messages=[
                    {"role": "system", "content": relevance_prompt},
                    {"role": "user", "content": prompt_text},
                ],
                response_format={"type": "json_object"},
            )
            loaded = json.loads(result.choices[0].message.content)
            relevant = loaded.get("relevant", True)
            paper_db.update(record["id"], {"relevant": relevant})
            if not relevant:
                logger.info(f"Marked irrelevant: {title}")
        except Exception as e:
            logger.error(f"Error classifying {record['id']}: {e}")
            # Default to relevant on error to avoid dropping good papers
            paper_db.update(record["id"], {"relevant": True})
