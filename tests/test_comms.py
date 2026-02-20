"""Tests for secnews.utils_comms — markdown formatting and share_results."""

import re
from pathlib import Path

import pytest

from secnews.utils_comms import _format_record_markdown, share_results


# ---------------------------------------------------------------------------
# Markdown formatting
# ---------------------------------------------------------------------------


class TestFormatRecordMarkdown:

    def test_output_structure(self, sample_summarized_record):
        md = _format_record_markdown(sample_summarized_record)
        r = sample_summarized_record

        # Contains title in bold
        assert f"**{r['title']}**" in md
        # Contains source link
        assert f"[source]({r['url']})" in md
        # Contains tag
        assert f"#{r['tag']}" in md
        # Contains one-liner
        assert r["one_liner"] in md
        # Contains all findings as bullet points
        for point in r["points"]:
            assert f" - {point}" in md
        # Contains authors and affiliations
        for author in r["authors"]:
            assert author in md
        for affiliation in r["affiliations"]:
            assert affiliation in md
        # Ends with separator
        assert md.strip().endswith("<br>")

    def test_special_characters_in_title(self):
        """Titles with markdown-sensitive characters should not break format."""
        record = {
            "emoji": "🔥",
            "title": 'Paper with **bold** and [link](http://x) and "quotes"',
            "url": "http://arxiv.org/pdf/test.pdf",
            "tag": "security",
            "one_liner": "Has special chars.",
            "points": ["Point with *asterisks*.", "Point with `backticks`."],
        }
        md = _format_record_markdown(record)
        # Should not raise, and should contain the title
        assert "Paper with **bold**" in md

    def test_emoji_present(self, sample_summarized_record):
        md = _format_record_markdown(sample_summarized_record)
        assert md.startswith(sample_summarized_record["emoji"])


# ---------------------------------------------------------------------------
# share_results
# ---------------------------------------------------------------------------


class TestShareResults:

    def test_writes_markdown_file(self, tmp_path, tmp_db, sample_summarized_record):
        """share_results creates a dated .md file with correct content."""
        tmp_db.insert(sample_summarized_record)

        summaries_path = str(tmp_path / "summaries")
        result = share_results(
            pull_window="2026-01-01T00:00:00Z",
            paper_db=tmp_db,
            summaries_path=summaries_path,
        )
        assert result is True

        # Find the created .md file
        md_files = list(Path(summaries_path).glob("*.md"))
        assert len(md_files) == 1

        content = md_files[0].read_text()
        assert sample_summarized_record["title"] in content
        assert sample_summarized_record["one_liner"] in content

    def test_writes_eml_file(self, tmp_path, tmp_db, sample_summarized_record):
        """share_results creates a dated .eml file for Outlook."""
        tmp_db.insert(sample_summarized_record)

        summaries_path = str(tmp_path / "summaries")
        share_results(
            pull_window="2026-01-01T00:00:00Z",
            paper_db=tmp_db,
            summaries_path=summaries_path,
        )

        eml_files = list(Path(summaries_path).glob("*.eml"))
        assert len(eml_files) == 1

        content = eml_files[0].read_text()
        assert "Subject:" in content
        assert "AIRT Gen AI Security News" in content
        assert "Content-Type: text/html" in content

    def test_returns_false_when_no_records(self, tmp_db, tmp_path):
        """If no summarized records exist in the window, returns False."""
        result = share_results(
            pull_window="2026-01-01T00:00:00Z",
            paper_db=tmp_db,
            summaries_path=str(tmp_path / "summaries"),
        )
        assert result is False
        # No file created
        assert not (tmp_path / "summaries").exists()

    def test_multiple_records_all_present(self, tmp_path, tmp_db):
        """All summarized records in the window should appear in the output."""
        for i in range(3):
            tmp_db.insert({
                "id": f"multi-{i}",
                "url": f"http://arxiv.org/pdf/multi-{i}.pdf",
                "published": "2026-02-10T00:00:00Z",
                "title": f"Multi Paper {i}",
                "downloaded": True,
                "summarized": True,
                "emoji": "📄",
                "tag": "security",
                "relevant": True,
                "one_liner": f"One-liner for paper {i}.",
                "points": [f"Point A-{i}", f"Point B-{i}", f"Point C-{i}"],
            })

        summaries_path = str(tmp_path / "summaries")
        share_results(
            pull_window="2026-02-01T00:00:00Z",
            paper_db=tmp_db,
            summaries_path=summaries_path,
        )

        content = list(Path(summaries_path).glob("*.md"))[0].read_text()
        for i in range(3):
            assert f"Multi Paper {i}" in content
            assert f"One-liner for paper {i}" in content

    def test_excludes_records_outside_window(self, tmp_path, tmp_db):
        """Records older than pull_window should not appear in output."""
        tmp_db.insert({
            "id": "old",
            "url": "http://arxiv.org/pdf/old.pdf",
            "published": "2025-01-01T00:00:00Z",
            "title": "Old Paper",
            "downloaded": True,
            "summarized": True,
            "emoji": "📄",
            "tag": "security",
            "relevant": True,
            "one_liner": "Should not appear.",
            "points": ["A", "B", "C"],
        })
        tmp_db.insert({
            "id": "new",
            "url": "http://arxiv.org/pdf/new.pdf",
            "published": "2026-02-15T00:00:00Z",
            "title": "New Paper",
            "downloaded": True,
            "summarized": True,
            "emoji": "📄",
            "tag": "security",
            "relevant": True,
            "one_liner": "Should appear.",
            "points": ["X", "Y", "Z"],
        })

        summaries_path = str(tmp_path / "summaries")
        share_results(
            pull_window="2026-02-01T00:00:00Z",
            paper_db=tmp_db,
            summaries_path=summaries_path,
        )

        content = list(Path(summaries_path).glob("*.md"))[0].read_text()
        assert "New Paper" in content
        assert "Old Paper" not in content

    def test_filters_irrelevant_papers_by_default(self, tmp_path, tmp_db):
        """Papers marked irrelevant are excluded from output by default."""
        tmp_db.insert({
            "id": "rel",
            "url": "http://arxiv.org/pdf/rel.pdf",
            "published": "2026-02-15T00:00:00Z",
            "title": "Relevant Paper",
            "downloaded": True,
            "summarized": True,
            "emoji": "🛡️",
            "tag": "security",
            "relevant": True,
            "one_liner": "Security stuff.",
            "points": ["A", "B", "C"],
        })
        tmp_db.insert({
            "id": "irrel",
            "url": "http://arxiv.org/pdf/irrel.pdf",
            "published": "2026-02-15T00:00:00Z",
            "title": "Irrelevant Paper",
            "downloaded": True,
            "summarized": True,
            "emoji": "📄",
            "tag": "security",
            "relevant": False,
            "one_liner": "Not relevant.",
            "points": ["X", "Y", "Z"],
        })

        summaries_path = str(tmp_path / "summaries")
        share_results(
            pull_window="2026-02-01T00:00:00Z",
            paper_db=tmp_db,
            summaries_path=summaries_path,
        )

        content = list(Path(summaries_path).glob("*.md"))[0].read_text()
        assert "Relevant Paper" in content
        assert "Irrelevant Paper" not in content

    def test_include_all_flag(self, tmp_path, tmp_db):
        """With include_all=True, all papers are included."""
        tmp_db.insert({
            "id": "irrel2",
            "url": "http://arxiv.org/pdf/irrel2.pdf",
            "published": "2026-02-15T00:00:00Z",
            "title": "Irrelevant Paper Two",
            "downloaded": True,
            "summarized": True,
            "emoji": "📄",
            "tag": "general",
            "relevant": False,
            "one_liner": "Included now.",
            "points": ["A", "B", "C"],
        })

        summaries_path = str(tmp_path / "summaries")
        share_results(
            pull_window="2026-02-01T00:00:00Z",
            paper_db=tmp_db,
            summaries_path=summaries_path,
            include_all=True,
        )

        content = list(Path(summaries_path).glob("*.md"))[0].read_text()
        assert "Irrelevant Paper Two" in content


# ---------------------------------------------------------------------------
# Round-trip: markdown output can be parsed back
# ---------------------------------------------------------------------------


class TestMarkdownRoundTrip:

    PATTERN = re.compile(
        r"^(.+?) \*\*(.+?)\*\* \[source\]\((.+?)\) #(\w+)\s*\n"
        r"(?:\n \*.+?\*\n)?"  # optional authors/affiliations line
        r"\n (.+?)$((?:\n - .+$)+)",
        re.MULTILINE,
    )

    def test_parseable_output(self, sample_summarized_record):
        """The markdown output should be parseable by the migrate.py regex pattern."""
        md = _format_record_markdown(sample_summarized_record)
        match = self.PATTERN.search(md)
        assert match is not None, f"Regex failed to match:\n{md}"

        emoji = match.group(1).strip()
        title = match.group(2).strip()
        url = match.group(3).strip()
        tag = match.group(4).strip()
        one_liner = match.group(5).strip()
        points_raw = match.group(6).strip()
        points = [p.strip("- ").strip() for p in points_raw.split("\n") if p.strip()]

        r = sample_summarized_record
        assert emoji == r["emoji"]
        assert title == r["title"]
        assert url == r["url"]
        assert tag == r["tag"]
        assert one_liner == r["one_liner"]
        assert points == r["points"]
