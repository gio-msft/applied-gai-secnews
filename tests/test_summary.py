"""Tests for secnews.utils_summary — mocked and real LLM summarization."""

import os
import json
import shutil
from unittest.mock import MagicMock, patch

import pytest

from secnews.utils_summary import summarize_records, _validate_affiliations, classify_relevance, classify_project_relevance
from tests.conftest import REAL_PDF_ID, PAPERS_DIR


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_summarizer(response_json):
    """Create a mock Azure OpenAI client that returns the given JSON dict."""
    summarizer = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps(response_json)
    mock_result = MagicMock()
    mock_result.choices = [mock_choice]
    summarizer.chat.completions.create.return_value = mock_result
    return summarizer


VALID_LLM_RESPONSE = {
    "findings": [
        "Finding one about vulnerability detection.",
        "Finding two about attack surface reduction.",
        "Finding three about deployment implications.",
    ],
    "one_liner": "A novel approach to LLM security.",
    "emoji": "🛡️",
    "tag": "security",
    "affiliations": ["MIT", "Stanford University"],
}


# ---------------------------------------------------------------------------
# Mocked LLM tests
# ---------------------------------------------------------------------------


class TestSummarizeRecordsMocked:

    def test_happy_path(self, tmp_path, tmp_db):
        """With a real PDF and mocked LLM, the record is correctly summarized."""
        paper_path = str(tmp_path / "papers")
        os.makedirs(paper_path)
        # Copy a real PDF into tmp
        src = os.path.join(PAPERS_DIR, f"{REAL_PDF_ID}.pdf")
        shutil.copy(src, os.path.join(paper_path, f"{REAL_PDF_ID}.pdf"))

        tmp_db.insert({
            "id": REAL_PDF_ID,
            "url": f"http://arxiv.org/pdf/{REAL_PDF_ID}.pdf",
            "published": "2026-02-10T00:00:00Z",
            "title": "Test Paper",
            "authors": [],  # empty authors = validation skipped
            "downloaded": True,
            "summarized": False,
        })

        summarizer = _make_mock_summarizer(VALID_LLM_RESPONSE)
        summarize_records(
            records=tmp_db.find(summarized=False),
            summarizer=summarizer,
            summarizer_prompt="Test prompt",
            paper_path=paper_path,
            paper_db=tmp_db,
        )

        rec = tmp_db.find(summarized=True)
        assert len(rec) == 1
        assert rec[0]["summarized"] is True
        assert rec[0]["emoji"] == "🛡️"
        assert rec[0]["tag"] == "security"
        assert len(rec[0]["points"]) == 3
        assert rec[0]["one_liner"] == "A novel approach to LLM security."
        assert rec[0]["affiliations"] == ["MIT", "Stanford University"]

    def test_missing_emoji_and_tag_get_defaults(self, tmp_path, tmp_db):
        """If LLM omits emoji/tag, defaults are applied."""
        paper_path = str(tmp_path / "papers")
        os.makedirs(paper_path)
        shutil.copy(
            os.path.join(PAPERS_DIR, f"{REAL_PDF_ID}.pdf"),
            os.path.join(paper_path, f"{REAL_PDF_ID}.pdf"),
        )
        tmp_db.insert({
            "id": REAL_PDF_ID,
            "url": f"http://arxiv.org/pdf/{REAL_PDF_ID}.pdf",
            "published": "2026-02-10T00:00:00Z",
            "title": "Test",
            "authors": ["Alice Smith"],
            "downloaded": True,
            "summarized": False,
        })

        # Response without emoji and tag keys
        response = {
            "findings": ["A", "B", "C"],
            "one_liner": "Interesting paper.",
        }
        summarizer = _make_mock_summarizer(response)
        summarize_records(
            records=tmp_db.find(summarized=False),
            summarizer=summarizer,
            summarizer_prompt="Test prompt",
            paper_path=paper_path,
            paper_db=tmp_db,
        )

        rec = tmp_db.find(summarized=True)[0]
        assert rec["emoji"] == "🔍"
        assert rec["tag"] == "general"

    def test_malformed_json_skips_record(self, tmp_path, tmp_db):
        """If LLM returns invalid JSON, the record is NOT marked summarized."""
        paper_path = str(tmp_path / "papers")
        os.makedirs(paper_path)
        shutil.copy(
            os.path.join(PAPERS_DIR, f"{REAL_PDF_ID}.pdf"),
            os.path.join(paper_path, f"{REAL_PDF_ID}.pdf"),
        )
        tmp_db.insert({
            "id": REAL_PDF_ID,
            "url": f"http://arxiv.org/pdf/{REAL_PDF_ID}.pdf",
            "published": "2026-02-10T00:00:00Z",
            "title": "Test",
            "downloaded": True,
            "summarized": False,
        })

        # Mock returns non-JSON
        summarizer = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "This is not JSON at all"
        mock_result = MagicMock()
        mock_result.choices = [mock_choice]
        summarizer.chat.completions.create.return_value = mock_result

        summarize_records(
            records=tmp_db.find(summarized=False),
            summarizer=summarizer,
            summarizer_prompt="Test prompt",
            paper_path=paper_path,
            paper_db=tmp_db,
        )

        # Record should still be unsummarized
        assert len(tmp_db.find(summarized=True)) == 0
        assert len(tmp_db.find(summarized=False)) == 1

    def test_corrupt_pdf_skips_record(self, tmp_path, tmp_db):
        """A file with garbage bytes should be skipped, not crash the pipeline."""
        paper_path = str(tmp_path / "papers")
        os.makedirs(paper_path)
        # Write garbage
        with open(os.path.join(paper_path, f"{REAL_PDF_ID}.pdf"), "wb") as f:
            f.write(b"NOT A PDF AT ALL")

        tmp_db.insert({
            "id": REAL_PDF_ID,
            "url": f"http://arxiv.org/pdf/{REAL_PDF_ID}.pdf",
            "published": "2026-02-10T00:00:00Z",
            "title": "Test",
            "downloaded": True,
            "summarized": False,
        })

        summarizer = _make_mock_summarizer(VALID_LLM_RESPONSE)
        # Should not raise
        summarize_records(
            records=tmp_db.find(summarized=False),
            summarizer=summarizer,
            summarizer_prompt="Test prompt",
            paper_path=paper_path,
            paper_db=tmp_db,
        )
        # Record should remain unsummarized
        assert len(tmp_db.find(summarized=True)) == 0

    @patch("secnews.utils_summary.download_paper")
    def test_missing_pdf_triggers_fallback_download(self, mock_dl, tmp_path, tmp_db):
        """If the PDF doesn't exist, download_paper is called as fallback."""
        paper_path = str(tmp_path / "papers")
        os.makedirs(paper_path)

        tmp_db.insert({
            "id": REAL_PDF_ID,
            "url": f"http://arxiv.org/pdf/{REAL_PDF_ID}.pdf",
            "published": "2026-02-10T00:00:00Z",
            "title": "Test",
            "downloaded": False,
            "summarized": False,
        })

        # Make the fallback download actually place the real PDF
        def do_download(url, paper_db, pp):
            shutil.copy(
                os.path.join(PAPERS_DIR, f"{REAL_PDF_ID}.pdf"),
                os.path.join(pp, f"{REAL_PDF_ID}.pdf"),
            )
            return True

        mock_dl.side_effect = do_download

        summarizer = _make_mock_summarizer(VALID_LLM_RESPONSE)
        summarize_records(
            records=tmp_db.find(summarized=False),
            summarizer=summarizer,
            summarizer_prompt="Test prompt",
            paper_path=paper_path,
            paper_db=tmp_db,
        )

        mock_dl.assert_called_once()
        assert len(tmp_db.find(summarized=True)) == 1


# ---------------------------------------------------------------------------
# Author/affiliation validation
# ---------------------------------------------------------------------------


class TestClassifyRelevance:

    def _make_classifier(self, relevant):
        classifier = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = json.dumps({"relevant": relevant})
        mock_result = MagicMock()
        mock_result.choices = [mock_choice]
        classifier.chat.completions.create.return_value = mock_result
        return classifier

    def test_marks_relevant_true(self, tmp_db):
        tmp_db.insert({
            "id": "r1", "url": "http://x.pdf",
            "published": "2026-02-15T00:00:00Z",
            "title": "Jailbreak Attack on LLMs",
            "downloaded": True, "summarized": True,
            "tag": "security", "one_liner": "About jailbreaks.",
            "emoji": "🛡️", "points": ["A"],
        })
        classify_relevance(
            records=tmp_db.find(summarized=True),
            classifier=self._make_classifier(True),
            relevance_prompt="test",
            paper_db=tmp_db,
        )
        assert tmp_db.find()[0]["relevant"] is True

    def test_marks_relevant_false(self, tmp_db):
        tmp_db.insert({
            "id": "r2", "url": "http://x.pdf",
            "published": "2026-02-15T00:00:00Z",
            "title": "ZK Proofs for ML",
            "downloaded": True, "summarized": True,
            "tag": "security", "one_liner": "About zero knowledge.",
            "emoji": "🛡️", "points": ["A"],
        })
        classify_relevance(
            records=tmp_db.find(summarized=True),
            classifier=self._make_classifier(False),
            relevance_prompt="test",
            paper_db=tmp_db,
        )
        assert tmp_db.find()[0]["relevant"] is False

    def test_general_tag_auto_irrelevant(self, tmp_db):
        """Papers tagged 'general' are auto-marked irrelevant without an LLM call."""
        tmp_db.insert({
            "id": "g1", "url": "http://x.pdf",
            "published": "2026-02-15T00:00:00Z",
            "title": "Multi-armed Bandits",
            "downloaded": True, "summarized": True,
            "tag": "general", "one_liner": "About bandits.",
            "emoji": "🎰", "points": ["A"],
        })
        # Classifier should NOT be called
        classifier = MagicMock()
        classify_relevance(
            records=tmp_db.find(summarized=True),
            classifier=classifier,
            relevance_prompt="test",
            paper_db=tmp_db,
        )
        classifier.chat.completions.create.assert_not_called()
        assert tmp_db.find()[0]["relevant"] is False

    def test_skips_already_classified(self, tmp_db):
        """Records with 'relevant' already set are not re-classified."""
        tmp_db.insert({
            "id": "s1", "url": "http://x.pdf",
            "published": "2026-02-15T00:00:00Z",
            "title": "Already Classified",
            "downloaded": True, "summarized": True,
            "tag": "security", "one_liner": "Done.",
            "emoji": "🛡️", "points": ["A"],
            "relevant": True,
        })
        classifier = MagicMock()
        classify_relevance(
            records=tmp_db.find(summarized=True),
            classifier=classifier,
            relevance_prompt="test",
            paper_db=tmp_db,
        )
        classifier.chat.completions.create.assert_not_called()

    def test_defaults_to_relevant_on_error(self, tmp_db):
        """On LLM error, paper is marked relevant to avoid dropping good papers."""
        tmp_db.insert({
            "id": "e1", "url": "http://x.pdf",
            "published": "2026-02-15T00:00:00Z",
            "title": "Error Paper",
            "downloaded": True, "summarized": True,
            "tag": "security", "one_liner": "Something.",
            "emoji": "🛡️", "points": ["A"],
        })
        classifier = MagicMock()
        classifier.chat.completions.create.side_effect = Exception("API down")
        classify_relevance(
            records=tmp_db.find(summarized=True),
            classifier=classifier,
            relevance_prompt="test",
            paper_db=tmp_db,
        )
        assert tmp_db.find()[0]["relevant"] is True


class TestClassifyProjectRelevance:

    def _make_classifier(self, projects):
        classifier = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = json.dumps({"projects": projects})
        mock_result = MagicMock()
        mock_result.choices = [mock_choice]
        classifier.chat.completions.create.return_value = mock_result
        return classifier

    def _insert_record(self, tmp_db, record_id="p1"):
        tmp_db.insert({
            "id": record_id, "url": "http://x.pdf",
            "published": "2026-02-15T00:00:00Z",
            "title": "Jailbreak Attack on LLMs",
            "downloaded": True, "summarized": True,
            "tag": "security", "one_liner": "About jailbreaks.",
            "emoji": "🛡️", "points": ["A"], "relevant": True,
        })

    def test_matches_valid_projects(self, tmp_db):
        """LLM returns valid project IDs — stored correctly."""
        self._insert_record(tmp_db)
        classify_project_relevance(
            records=tmp_db.find(summarized=True),
            classifier=self._make_classifier(["proj-alpha", "proj-beta"]),
            prompt="test",
            project_ids=["proj-alpha", "proj-beta", "proj-gamma"],
            paper_db=tmp_db,
        )
        assert tmp_db.find()[0]["projects"] == ["proj-alpha", "proj-beta"]

    def test_no_matches_returns_empty(self, tmp_db):
        """LLM returns no matches — stored as empty list."""
        self._insert_record(tmp_db)
        classify_project_relevance(
            records=tmp_db.find(summarized=True),
            classifier=self._make_classifier([]),
            prompt="test",
            project_ids=["proj-alpha"],
            paper_db=tmp_db,
        )
        assert tmp_db.find()[0]["projects"] == []

    def test_strips_hallucinated_ids(self, tmp_db):
        """LLM returns unknown project IDs — hallucinated IDs are stripped."""
        self._insert_record(tmp_db)
        classify_project_relevance(
            records=tmp_db.find(summarized=True),
            classifier=self._make_classifier(["proj-alpha", "hallucinated-proj"]),
            prompt="test",
            project_ids=["proj-alpha", "proj-beta"],
            paper_db=tmp_db,
        )
        assert tmp_db.find()[0]["projects"] == ["proj-alpha"]

    def test_skips_already_classified(self, tmp_db):
        """Records with 'projects' already set are not re-classified."""
        self._insert_record(tmp_db)
        tmp_db.update("p1", {"projects": ["proj-alpha"]})
        classifier = MagicMock()
        classify_project_relevance(
            records=tmp_db.find(summarized=True),
            classifier=classifier,
            prompt="test",
            project_ids=["proj-alpha"],
            paper_db=tmp_db,
        )
        classifier.chat.completions.create.assert_not_called()

    def test_defaults_to_empty_on_error(self, tmp_db):
        """On LLM error, paper gets empty projects list."""
        self._insert_record(tmp_db)
        classifier = MagicMock()
        classifier.chat.completions.create.side_effect = Exception("API down")
        classify_project_relevance(
            records=tmp_db.find(summarized=True),
            classifier=classifier,
            prompt="test",
            project_ids=["proj-alpha"],
            paper_db=tmp_db,
        )
        assert tmp_db.find()[0]["projects"] == []


class TestValidateAffiliations:

    def test_keeps_affiliations_when_authors_match(self):
        """Affiliations are kept when arXiv authors appear in PDF text."""
        affiliations = ["MIT", "Stanford"]
        authors = ["Alice Smith", "Bob Jones"]
        pdf_text = "Alice Smith and Bob Jones from MIT and Stanford..."
        result = _validate_affiliations(affiliations, authors, pdf_text, "test")
        assert result == ["MIT", "Stanford"]

    def test_discards_affiliations_when_authors_dont_match(self):
        """Affiliations are discarded when arXiv authors are not in the PDF."""
        affiliations = ["MIT", "Stanford"]
        authors = ["Alice Smith", "Bob Jones"]
        pdf_text = "Completely unrelated text with no author names at all..."
        result = _validate_affiliations(affiliations, authors, pdf_text, "test")
        assert result == []

    def test_partial_match_above_threshold(self):
        """At least 50% of authors matching keeps the affiliations."""
        affiliations = ["MIT"]
        authors = ["Alice Smith", "Bob Jones"]
        # Only Smith appears
        pdf_text = "Smith et al. present a study on LLM security..."
        result = _validate_affiliations(affiliations, authors, pdf_text, "test")
        assert result == ["MIT"]

    def test_partial_match_below_threshold(self):
        """Below 50% match discards affiliations."""
        affiliations = ["MIT"]
        authors = ["Alice Smith", "Bob Jones", "Charlie Brown"]
        # Only Smith appears (1/3 = 33%)
        pdf_text = "Smith et al. present a study on LLM security..."
        result = _validate_affiliations(affiliations, authors, pdf_text, "test")
        assert result == []

    def test_empty_authors_returns_affiliations_unchanged(self):
        """If no arXiv authors, skip validation and return affiliations as-is."""
        affiliations = ["MIT"]
        result = _validate_affiliations(affiliations, [], "any text", "test")
        assert result == ["MIT"]

    def test_empty_affiliations_returns_empty(self):
        """Empty affiliations stay empty regardless of authors."""
        result = _validate_affiliations([], ["Alice Smith"], "Alice Smith...", "test")
        assert result == []


# ---------------------------------------------------------------------------
# Real LLM integration test (requires Azure OpenAI credentials)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_real_llm_summarization(tmp_path, tmp_db):
    """End-to-end test with real Azure OpenAI. Run with: pytest -m integration"""
    import dotenv
    from openai import AzureOpenAI

    dotenv.load_dotenv(".env")

    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    api_key = os.environ.get("AZURE_OPENAI_API_KEY")
    model = os.environ.get("AZURE_OPENAI_SUMMARY_MODEL_NAME")
    if not all([endpoint, api_key, model]):
        pytest.skip("Azure OpenAI credentials not configured")

    paper_path = str(tmp_path / "papers")
    os.makedirs(paper_path)
    shutil.copy(
        os.path.join(PAPERS_DIR, f"{REAL_PDF_ID}.pdf"),
        os.path.join(paper_path, f"{REAL_PDF_ID}.pdf"),
    )

    tmp_db.insert({
        "id": REAL_PDF_ID,
        "url": f"http://arxiv.org/pdf/{REAL_PDF_ID}.pdf",
        "published": "2026-02-10T00:00:00Z",
        "title": "Integration Test Paper",
        "downloaded": True,
        "summarized": False,
    })

    oai = AzureOpenAI(
        azure_endpoint=endpoint,
        api_key=api_key,
        api_version="2025-01-01-preview",
    )

    system_prompt = """Assume the role of a technical writer. 
Format the output as a JSON object with:
'findings' // array of 3 single-sentence findings.
'one_liner' // one-liner summary
'emoji' // a single emoji
'tag' // 'security', 'cyber', or 'general'"""

    summarize_records(
        records=tmp_db.find(summarized=False),
        summarizer=oai,
        summarizer_prompt=system_prompt,
        paper_path=paper_path,
        paper_db=tmp_db,
    )

    results = tmp_db.find(summarized=True)
    assert len(results) == 1

    rec = results[0]
    assert isinstance(rec["points"], list)
    assert len(rec["points"]) == 3
    assert isinstance(rec["one_liner"], str) and len(rec["one_liner"]) > 10
    assert isinstance(rec["emoji"], str) and len(rec["emoji"]) >= 1
    assert rec["tag"] in ("security", "cyber", "general")
