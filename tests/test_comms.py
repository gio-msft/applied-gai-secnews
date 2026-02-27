"""Tests for secnews.utils_comms — markdown formatting and share_results."""

import re
from pathlib import Path

import pytest

from secnews.utils_comms import (
    _format_authors,
    _format_record_html,
    _format_record_markdown,
    share_results,
)


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
        # Contains interest score
        assert "`7/10`" in md
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

    def test_many_authors_truncated_markdown(self):
        """More than 3 authors should show first 3 + 'et al.' in markdown."""
        record = {
            "emoji": "📄",
            "title": "Many Authors Paper",
            "url": "http://arxiv.org/pdf/test.pdf",
            "tag": "security",
            "one_liner": "Summary.",
            "points": ["A"],
            "authors": ["Alice", "Bob", "Carol", "Dave", "Eve"],
            "affiliations": ["MIT"],
        }
        md = _format_record_markdown(record)
        assert "Alice, Bob, Carol et al." in md
        assert "Dave" not in md
        assert "Eve" not in md
        assert "(MIT)" in md

    def test_many_authors_truncated_html(self):
        """More than 3 authors should show first 3 + 'et al.' in HTML."""
        record = {
            "emoji": "📄",
            "title": "Many Authors Paper",
            "url": "http://arxiv.org/pdf/test.pdf",
            "tag": "security",
            "one_liner": "Summary.",
            "points": ["A"],
            "authors": ["Alice", "Bob", "Carol", "Dave", "Eve"],
            "affiliations": ["MIT"],
        }
        html = _format_record_html(record)
        assert "Alice, Bob, Carol et al." in html
        assert "Dave" not in html
        assert "Eve" not in html
        assert "(MIT)" in html

    def test_three_or_fewer_authors_not_truncated(self):
        """Exactly 3 authors should all appear without 'et al.'."""
        record = {
            "emoji": "📄",
            "title": "Three Authors",
            "url": "http://arxiv.org/pdf/test.pdf",
            "tag": "security",
            "one_liner": "Summary.",
            "points": ["A"],
            "authors": ["Alice", "Bob", "Carol"],
            "affiliations": ["MIT"],
        }
        md = _format_record_markdown(record)
        assert "Alice, Bob, Carol" in md
        assert "et al." not in md

    def test_projects_shown_when_present(self):
        """Papers with matched projects show a project line at the bottom."""
        record = {
            "emoji": "📄",
            "title": "Paper With Projects",
            "url": "http://arxiv.org/pdf/test.pdf",
            "tag": "security",
            "one_liner": "Summary.",
            "points": ["A"],
            "projects": ["proj-alpha", "proj-beta"],
        }
        md = _format_record_markdown(record)
        assert "📌" in md
        assert "proj-alpha" in md
        assert "proj-beta" in md
        # Project line should come after findings but before separator
        proj_pos = md.index("proj-alpha")
        point_pos = md.index(" - A")
        br_pos = md.index("<br>")
        assert point_pos < proj_pos < br_pos

    def test_no_projects_line_when_empty(self):
        """Papers with no matched projects don't show the project line."""
        record = {
            "emoji": "📄",
            "title": "Paper Without Projects",
            "url": "http://arxiv.org/pdf/test.pdf",
            "tag": "security",
            "one_liner": "Summary.",
            "points": ["A"],
            "projects": [],
        }
        md = _format_record_markdown(record)
        assert "📌" not in md
        assert "Projects:" not in md

    def test_no_projects_key_no_line(self):
        """Papers without the projects key at all don't show the project line."""
        record = {
            "emoji": "📄",
            "title": "Legacy Paper",
            "url": "http://arxiv.org/pdf/test.pdf",
            "tag": "security",
            "one_liner": "Summary.",
            "points": ["A"],
        }
        md = _format_record_markdown(record)
        assert "📌" not in md

    def test_projects_in_html(self):
        """Matched projects appear in HTML output too."""
        record = {
            "emoji": "📄",
            "title": "HTML Projects Paper",
            "url": "http://arxiv.org/pdf/test.pdf",
            "tag": "security",
            "one_liner": "Summary.",
            "points": ["A"],
            "projects": ["proj-gamma"],
        }
        html = _format_record_html(record)
        assert "proj-gamma" in html
        assert "📌" in html

    def test_no_projects_in_html_when_empty(self):
        """No project line in HTML when projects list is empty."""
        record = {
            "emoji": "📄",
            "title": "HTML No Projects",
            "url": "http://arxiv.org/pdf/test.pdf",
            "tag": "security",
            "one_liner": "Summary.",
            "points": ["A"],
            "projects": [],
        }
        html = _format_record_html(record)
        assert "Projects:" not in html

    def test_score_not_shown_when_absent(self):
        """Papers without interest_score should not have a score indicator."""
        record = {
            "emoji": "📄",
            "title": "Legacy Paper",
            "url": "http://arxiv.org/pdf/test.pdf",
            "tag": "security",
            "one_liner": "Summary.",
            "points": ["A"],
        }
        md = _format_record_markdown(record)
        assert "/10`" not in md
        html = _format_record_html(record)
        assert "/10</span>" not in html

    def test_score_shown_in_markdown(self):
        """Papers with interest_score show the score in markdown output."""
        record = {
            "emoji": "📄",
            "title": "Scored Paper",
            "url": "http://arxiv.org/pdf/test.pdf",
            "tag": "security",
            "one_liner": "Summary.",
            "points": ["A"],
            "interest_score": 9,
        }
        md = _format_record_markdown(record)
        assert "`9/10`" in md

    def test_score_shown_in_html(self):
        """Papers with interest_score show the score in HTML output."""
        record = {
            "emoji": "📄",
            "title": "Scored Paper",
            "url": "http://arxiv.org/pdf/test.pdf",
            "tag": "security",
            "one_liner": "Summary.",
            "points": ["A"],
            "interest_score": 9,
        }
        html = _format_record_html(record)
        assert "9/10</span>" in html


class TestFormatAuthors:

    def test_empty_list(self):
        assert _format_authors([]) == ""

    def test_single_author(self):
        assert _format_authors(["Alice"]) == "Alice"

    def test_two_authors(self):
        assert _format_authors(["Alice", "Bob"]) == "Alice, Bob"

    def test_three_authors(self):
        assert _format_authors(["A", "B", "C"]) == "A, B, C"

    def test_four_authors_truncated(self):
        assert _format_authors(["A", "B", "C", "D"]) == "A, B, C et al."

    def test_many_authors_truncated(self):
        assert _format_authors(["A", "B", "C", "D", "E", "F"]) == "A, B, C et al."


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

    def test_records_sorted_by_interest_score(self, tmp_path, tmp_db):
        """Papers should appear sorted by interest_score descending in output."""
        for score, title in [(3, "Low Score Paper"), (9, "High Score Paper"), (6, "Mid Score Paper")]:
            tmp_db.insert({
                "id": f"sort-{score}",
                "url": f"http://arxiv.org/pdf/sort-{score}.pdf",
                "published": "2026-02-10T00:00:00Z",
                "title": title,
                "downloaded": True,
                "summarized": True,
                "emoji": "📄",
                "tag": "security",
                "relevant": True,
                "one_liner": f"Score {score} paper.",
                "points": ["A"],
                "interest_score": score,
            })

        summaries_path = str(tmp_path / "summaries")
        share_results(
            pull_window="2026-02-01T00:00:00Z",
            paper_db=tmp_db,
            summaries_path=summaries_path,
        )

        content = list(Path(summaries_path).glob("*.md"))[0].read_text()
        pos_high = content.index("High Score Paper")
        pos_mid = content.index("Mid Score Paper")
        pos_low = content.index("Low Score Paper")
        assert pos_high < pos_mid < pos_low

    def test_legacy_records_without_score_get_default_position(self, tmp_path, tmp_db):
        """Records without interest_score sort as if they had score 5."""
        tmp_db.insert({
            "id": "high",
            "url": "http://arxiv.org/pdf/high.pdf",
            "published": "2026-02-10T00:00:00Z",
            "title": "High Score Paper",
            "downloaded": True,
            "summarized": True,
            "emoji": "📄",
            "tag": "security",
            "relevant": True,
            "one_liner": "High.",
            "points": ["A"],
            "interest_score": 9,
        })
        tmp_db.insert({
            "id": "legacy",
            "url": "http://arxiv.org/pdf/legacy.pdf",
            "published": "2026-02-10T00:00:00Z",
            "title": "Legacy No Score Paper",
            "downloaded": True,
            "summarized": True,
            "emoji": "📄",
            "tag": "security",
            "relevant": True,
            "one_liner": "Legacy.",
            "points": ["A"],
            # No interest_score field
        })
        tmp_db.insert({
            "id": "low",
            "url": "http://arxiv.org/pdf/low.pdf",
            "published": "2026-02-10T00:00:00Z",
            "title": "Low Score Paper",
            "downloaded": True,
            "summarized": True,
            "emoji": "📄",
            "tag": "security",
            "relevant": True,
            "one_liner": "Low.",
            "points": ["A"],
            "interest_score": 2,
        })

        summaries_path = str(tmp_path / "summaries")
        share_results(
            pull_window="2026-02-01T00:00:00Z",
            paper_db=tmp_db,
            summaries_path=summaries_path,
        )

        content = list(Path(summaries_path).glob("*.md"))[0].read_text()
        pos_high = content.index("High Score Paper")
        pos_legacy = content.index("Legacy No Score Paper")
        pos_low = content.index("Low Score Paper")
        # High (9) > Legacy (default 5) > Low (2)
        assert pos_high < pos_legacy < pos_low


# ---------------------------------------------------------------------------
# Round-trip: markdown output can be parsed back
# ---------------------------------------------------------------------------


class TestMarkdownRoundTrip:

    PATTERN = re.compile(
        r"^(.+?) \*\*(.+?)\*\* \[source\]\((.+?)\) #(\w+)(?:\s+`.+?`)? ?\s*\n"
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
