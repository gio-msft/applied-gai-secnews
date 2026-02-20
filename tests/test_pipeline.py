"""Integration test: full pipeline with mocked external services."""

import os
import json
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from secnews.utils_db import PaperDB
from secnews.utils_search import execute_searches, assemble_feeds, prune_feeds
from secnews.utils_papers import download_papers, assemble_records
from secnews.utils_summary import summarize_records, classify_relevance
from secnews.utils_comms import share_results
from tests.conftest import SAMPLE_ARXIV_FEED, PAPERS_DIR, REAL_PDF_ID


MOCK_LLM_RESPONSE = {
    "findings": ["F1", "F2", "F3"],
    "one_liner": "Pipeline test summary.",
    "emoji": "🧪",
    "tag": "security",
}


class TestFullPipeline:

    @patch("secnews.utils_papers._request_bulk")
    @patch("secnews.utils_search.requests.get")
    def test_end_to_end(self, mock_search_get, mock_bulk_dl, tmp_path):
        """Run the full pipeline with mocked arXiv search, mocked PDF download,
        and mocked LLM — verify DB state and markdown output at the end."""

        # --- Setup paths ---
        db_path = str(tmp_path / "papers.json")
        paper_path = str(tmp_path / "papers")
        summaries_path = str(tmp_path / "summaries")
        state_path = str(tmp_path / "search_state.json")
        os.makedirs(paper_path)

        paper_db = PaperDB(db_path)
        pull_window = "2026-01-10T00:00:00Z"

        # --- Mock arXiv search ---
        mock_response = MagicMock()
        mock_response.content = SAMPLE_ARXIV_FEED
        mock_search_get.return_value = mock_response

        # --- Step 1: Search ---
        params = [{"search_query": "test_query", "start": 0, "max_results": 200}]
        feeds = execute_searches(
            base="http://fake", params=params, state_path=state_path
        )
        assert len(feeds) == 1

        # --- Step 2: Assemble ---
        results = assemble_feeds(feeds=feeds, paper_db=paper_db)
        assert len(results) == 2
        assert len(paper_db.find()) == 2

        # --- Step 3: Prune ---
        valid = prune_feeds(
            feeds=results, pull_window=pull_window, paper_path=paper_path
        )
        assert len(valid) == 2  # both are recent and not on disk

        # --- Step 4: Download (mocked) ---
        # Read a real PDF's bytes to use as mock content
        real_pdf_bytes = open(
            os.path.join(PAPERS_DIR, f"{REAL_PDF_ID}.pdf"), "rb"
        ).read()

        mock_resp_1 = MagicMock()
        mock_resp_1.url = "http://arxiv.org/pdf/2601.00001v1.pdf"
        mock_resp_1.content = real_pdf_bytes

        mock_resp_2 = MagicMock()
        mock_resp_2.url = "http://arxiv.org/pdf/2601.00002v1.pdf"
        mock_resp_2.content = real_pdf_bytes

        mock_bulk_dl.return_value = [mock_resp_1, mock_resp_2]

        download_papers(results=valid, paper_db=paper_db, paper_path=paper_path)

        assert (tmp_path / "papers" / "2601.00001v1.pdf").exists()
        assert (tmp_path / "papers" / "2601.00002v1.pdf").exists()

        # --- Step 5: Assemble records ---
        records = assemble_records(pull_window=pull_window, paper_db=paper_db)
        assert len(records) == 2  # both downloaded, not yet summarized

        # --- Step 6: Summarize (mocked LLM) ---
        summarizer = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = json.dumps(MOCK_LLM_RESPONSE)
        mock_result = MagicMock()
        mock_result.choices = [mock_choice]
        summarizer.chat.completions.create.return_value = mock_result

        summarize_records(
            records=records,
            summarizer=summarizer,
            summarizer_prompt="Test prompt",
            paper_path=paper_path,
            paper_db=paper_db,
        )

        summarized = paper_db.find(summarized=True)
        assert len(summarized) == 2
        for rec in summarized:
            assert rec["emoji"] == "🧪"
            assert rec["points"] == ["F1", "F2", "F3"]

        # --- Step 6b: Classify relevance ---
        relevance_resp = MagicMock()
        rel_choice = MagicMock()
        rel_choice.message.content = json.dumps({"relevant": True})
        rel_result = MagicMock()
        rel_result.choices = [rel_choice]
        relevance_resp.chat.completions.create.return_value = rel_result

        classify_relevance(
            records=paper_db.find(summarized=True),
            classifier=relevance_resp,
            relevance_prompt="test",
            paper_db=paper_db,
        )

        # --- Step 7: Share ---
        result = share_results(
            pull_window=pull_window,
            paper_db=paper_db,
            summaries_path=summaries_path,
        )
        assert result is True

        md_files = list(Path(summaries_path).glob("*.md"))
        assert len(md_files) == 1

        content = md_files[0].read_text()
        assert "Test Paper Alpha" in content
        assert "Test Paper Beta" in content
        assert "Pipeline test summary." in content
        assert "🧪" in content

        # --- Verify final DB state ---
        all_records = paper_db.find()
        assert len(all_records) == 2
        for rec in all_records:
            assert rec["downloaded"] is True
            assert rec["summarized"] is True

    @patch("secnews.utils_search.requests.get")
    def test_rerun_skips_existing_papers(self, mock_search_get, tmp_path):
        """A second run with the same data should not re-process anything."""
        db_path = str(tmp_path / "papers.json")
        paper_path = str(tmp_path / "papers")
        state_path = str(tmp_path / "search_state.json")
        os.makedirs(paper_path)

        paper_db = PaperDB(db_path)

        mock_response = MagicMock()
        mock_response.content = SAMPLE_ARXIV_FEED
        mock_search_get.return_value = mock_response

        # First run: populate DB
        params = [{"search_query": "q", "start": 0, "max_results": 200}]
        feeds = execute_searches(
            base="http://fake", params=params, state_path=state_path
        )
        assemble_feeds(feeds=feeds, paper_db=paper_db)

        initial_count = len(paper_db.find())
        assert initial_count == 2

        # Second run: search should be cached
        feeds2 = execute_searches(
            base="http://fake", params=params, state_path=state_path
        )
        # No new feeds returned (cached)
        assert feeds2 == []

        # Even with force, DB should not get duplicates
        feeds3 = execute_searches(
            base="http://fake", params=params, state_path=state_path, force=True
        )
        assemble_feeds(feeds=feeds3, paper_db=paper_db)
        assert len(paper_db.find()) == initial_count  # no duplicates
