"""Tests for secnews.utils_papers — filename extraction, PDF reading, downloads."""

import os
from unittest.mock import patch, MagicMock

import pytest

from secnews.utils_papers import (
    _filename_from_url,
    _save,
    download_papers,
    assemble_records,
    read_pages,
)


# ---------------------------------------------------------------------------
# _filename_from_url
# ---------------------------------------------------------------------------


class TestFilenameFromUrl:

    def test_url_already_has_pdf_extension(self):
        assert _filename_from_url("http://arxiv.org/pdf/2505.14289v1.pdf") == "2505.14289v1.pdf"

    def test_url_without_pdf_extension(self):
        assert _filename_from_url("http://arxiv.org/pdf/2505.14289v1") == "2505.14289v1.pdf"

    def test_no_double_pdf(self):
        name = _filename_from_url("http://arxiv.org/pdf/2509.20230v2.pdf")
        assert name == "2509.20230v2.pdf"
        assert not name.endswith(".pdf.pdf")


# ---------------------------------------------------------------------------
# _save
# ---------------------------------------------------------------------------


class TestSave:

    def test_writes_binary_content(self, tmp_path):
        _save(str(tmp_path), "test.pdf", b"%PDF-fake-content")
        saved = (tmp_path / "test.pdf").read_bytes()
        assert saved == b"%PDF-fake-content"


# ---------------------------------------------------------------------------
# read_pages — real PDF
# ---------------------------------------------------------------------------


class TestReadPages:

    def test_reads_real_pdf(self, real_paper_path, real_pdf_id):
        from pypdf import PdfReader

        reader = PdfReader(os.path.join(real_paper_path, f"{real_pdf_id}.pdf"))
        result = read_pages(reader)
        assert result["pages"] > 0
        assert isinstance(result["content"], str)
        assert len(result["content"]) > 100  # real papers have substantial text
        assert result["characters"] == len(result["content"])

    def test_content_has_academic_keywords(self, real_paper_path, real_pdf_id):
        """Sanity check: extracted text should contain typical academic paper words."""
        from pypdf import PdfReader

        reader = PdfReader(os.path.join(real_paper_path, f"{real_pdf_id}.pdf"))
        result = read_pages(reader)
        text_lower = result["content"].lower()
        # At least some of these should appear in any academic PDF
        keywords = ["abstract", "introduction", "conclusion", "reference", "model", "attack"]
        matches = [kw for kw in keywords if kw in text_lower]
        assert len(matches) >= 2, f"Expected academic keywords, found only: {matches}"


# ---------------------------------------------------------------------------
# download_papers — mocked HTTP
# ---------------------------------------------------------------------------


class TestDownloadPapers:

    @patch("secnews.utils_papers._request_bulk")
    def test_downloads_saves_and_updates_db(self, mock_bulk, tmp_path, tmp_db):
        """Mocked bulk download should save files and update DB."""
        paper_path = str(tmp_path)

        # Insert a record into DB first
        tmp_db.insert({
            "id": "2601.00001v1",
            "url": "http://arxiv.org/pdf/2601.00001v1.pdf",
            "published": "2026-01-15T00:00:00Z",
            "title": "Test",
            "downloaded": False,
            "summarized": False,
        })

        # Mock response
        mock_response = MagicMock()
        mock_response.url = "http://arxiv.org/pdf/2601.00001v1.pdf"
        mock_response.content = b"%PDF-fake"
        mock_bulk.return_value = [mock_response]

        results = [{"url": "http://arxiv.org/pdf/2601.00001v1.pdf"}]
        download_papers(results=results, paper_db=tmp_db, paper_path=paper_path)

        # File saved
        assert (tmp_path / "2601.00001v1.pdf").exists()
        # DB updated
        rec = tmp_db.find()[0]
        assert rec["downloaded"] is True

    @patch("secnews.utils_papers._request_bulk")
    def test_deduplicates_urls(self, mock_bulk, tmp_path, tmp_db):
        """Duplicate URLs in results should only be downloaded once."""
        mock_bulk.return_value = []
        results = [
            {"url": "http://arxiv.org/pdf/2601.00001v1.pdf"},
            {"url": "http://arxiv.org/pdf/2601.00001v1.pdf"},
        ]
        download_papers(results=results, paper_db=tmp_db, paper_path=str(tmp_path))
        # _request_bulk should receive deduplicated list
        urls_arg = mock_bulk.call_args[0][0]
        assert len(urls_arg) == 1


# ---------------------------------------------------------------------------
# assemble_records
# ---------------------------------------------------------------------------


class TestAssembleRecords:

    def test_returns_unsummarized_in_window(self, tmp_db):
        tmp_db.insert({
            "id": "old_unsumm",
            "url": "http://example.com/1.pdf",
            "published": "2025-01-01T00:00:00Z",
            "title": "Old",
            "downloaded": True,
            "summarized": False,
        })
        tmp_db.insert({
            "id": "new_unsumm",
            "url": "http://example.com/2.pdf",
            "published": "2026-02-15T00:00:00Z",
            "title": "New Unsummarized",
            "downloaded": True,
            "summarized": False,
        })
        tmp_db.insert({
            "id": "new_summ",
            "url": "http://example.com/3.pdf",
            "published": "2026-02-15T00:00:00Z",
            "title": "New Summarized",
            "downloaded": True,
            "summarized": True,
        })

        records = assemble_records(
            pull_window="2026-02-01T00:00:00Z", paper_db=tmp_db
        )
        assert len(records) == 1
        assert records[0]["id"] == "new_unsumm"
