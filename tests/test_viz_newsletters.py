"""Tests for newsletter archive data exported by build_viz.py."""

import json

from build_viz import export_newsletters


def test_export_newsletters_adds_paper_ids_and_counts(tmp_path):
    summaries_dir = tmp_path / "summaries"
    summaries_dir.mkdir()
    output_path = tmp_path / "newsletters.json"

    (summaries_dir / "2026-05-01.md").write_text(
        """
**First Paper** [source](https://arxiv.org/pdf/2605.00001v1.pdf) #security `8/10`

Summary.

**Second Paper** [source](http://arxiv.org/pdf/2605.00002v2.pdf) #cyber `7/10`

Summary.

**Duplicate First Paper** [source](https://arxiv.org/pdf/2605.00001v1.pdf) #security `8/10`
""".strip(),
        encoding="utf-8",
    )

    entries = export_newsletters(str(summaries_dir), str(output_path))

    assert len(entries) == 1
    assert entries[0]["paper_ids"] == ["2605.00001v1", "2605.00002v2"]
    assert entries[0]["paper_count"] == 2
    assert 'data-paper-id="2605.00001v1"' in entries[0]["html"]

    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written[0]["paper_ids"] == ["2605.00001v1", "2605.00002v2"]
    assert written[0]["paper_count"] == 2
