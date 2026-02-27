import os
import pytest

# Root of the project
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAPERS_DIR = os.path.join(PROJECT_ROOT, "papers")

# A paper ID known to exist on disk with a valid PDF
REAL_PDF_ID = "2505.24201v1"


@pytest.fixture
def tmp_db(tmp_path):
    """A fresh PaperDB in a temporary directory."""
    from secnews.utils_db import PaperDB

    return PaperDB(str(tmp_path / "test_papers.json"))


@pytest.fixture
def real_paper_path():
    """Path to the real papers/ directory (read-only)."""
    assert os.path.isdir(PAPERS_DIR), f"papers/ directory not found at {PAPERS_DIR}"
    return PAPERS_DIR


@pytest.fixture
def real_pdf_id():
    """A known paper ID whose PDF exists on disk."""
    path = os.path.join(PAPERS_DIR, f"{REAL_PDF_ID}.pdf")
    assert os.path.isfile(path), f"Expected PDF not found: {path}"
    return REAL_PDF_ID


@pytest.fixture
def sample_summarized_record():
    """A realistic summarized record."""
    return {
        "id": "2601.99901v1",
        "url": "http://arxiv.org/pdf/2601.99901v1.pdf",
        "published": "2026-01-15T10:30:00Z",
        "title": "Test Paper: LLM Jailbreak Detection",
        "authors": ["Alice Smith", "Bob Jones"],
        "downloaded": True,
        "summarized": True,
        "emoji": "🛡️",
        "tag": "security",
        "one_liner": "A novel approach to detecting jailbreak attempts in LLMs.",
        "affiliations": ["MIT", "Stanford University"],
        "relevant": True,
        "projects": [],
        "interest_score": 7,
        "points": [
            "Finding one about jailbreak detection rates.",
            "Finding two about false positive reduction.",
            "Finding three about real-world deployment.",
        ],
    }


@pytest.fixture
def sample_unsummarized_record():
    """A realistic unsummarized record."""
    return {
        "id": "2601.99902v1",
        "url": "http://arxiv.org/pdf/2601.99902v1.pdf",
        "published": "2026-01-20T14:00:00Z",
        "title": "Test Paper: Prompt Injection Defense",
        "authors": ["Charlie Brown"],
        "downloaded": True,
        "summarized": False,
    }


SAMPLE_ARXIV_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
  <opensearch:totalResults>2</opensearch:totalResults>
  <opensearch:startIndex>0</opensearch:startIndex>
  <opensearch:itemsPerPage>200</opensearch:itemsPerPage>
  <entry>
    <id>http://arxiv.org/abs/2601.00001v1</id>
    <published>2026-01-15T10:30:00Z</published>
    <title>Test Paper Alpha: LLM Jailbreak</title>
    <author><name>Alice Smith</name></author>
    <author><name>Bob Jones</name></author>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2601.00002v1</id>
    <published>2026-01-20T14:00:00Z</published>
    <title>Test Paper Beta: Prompt Injection</title>
    <author><name>Charlie Brown</name></author>
  </entry>
</feed>"""
