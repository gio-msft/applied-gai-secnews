"""Tests for secnews.utils_db — PaperDB and date normalization."""

import json
import pytest
from secnews.utils_db import PaperDB, _normalize_date


# ---------------------------------------------------------------------------
# Date normalization
# ---------------------------------------------------------------------------


class TestNormalizeDate:
    """Verify _normalize_date produces consistent ISO 8601 output."""

    def test_z_suffix(self):
        assert _normalize_date("2025-05-01T00:00:00Z") == "2025-05-01T00:00:00Z"

    def test_plus_zero_offset(self):
        assert _normalize_date("2025-05-01T00:00:00+00:00") == "2025-05-01T00:00:00Z"

    def test_positive_offset_converts_to_utc(self):
        # 03:00 at +03:00 is midnight UTC
        assert _normalize_date("2025-05-01T03:00:00+03:00") == "2025-05-01T00:00:00Z"

    def test_negative_offset_converts_to_utc(self):
        # 21:00 at -03:00 on Apr 30 is midnight UTC on May 1
        assert _normalize_date("2025-04-30T21:00:00-03:00") == "2025-05-01T00:00:00Z"

    def test_consistent_with_search_normalize(self):
        """Guard against drift between the two duplicated normalizers."""
        from secnews.utils_search import _normalize_iso

        test_dates = [
            "2025-05-01T00:00:00Z",
            "2025-12-31T23:59:59Z",
            "2026-01-15T10:30:00+00:00",
            "2025-06-15T12:00:00+05:30",
        ]
        for d in test_dates:
            assert _normalize_date(d) == _normalize_iso(d), f"Mismatch for {d}"


# ---------------------------------------------------------------------------
# PaperDB — insert / has_url / find / update / persistence
# ---------------------------------------------------------------------------


class TestPaperDBInsert:

    def test_insert_and_has_url(self, tmp_db):
        record = {
            "id": "test1",
            "url": "http://example.com/test1.pdf",
            "published": "2026-01-15T10:00:00Z",
            "title": "Test 1",
            "downloaded": False,
            "summarized": False,
        }
        tmp_db.insert(record)
        assert tmp_db.has_url("http://example.com/test1.pdf")
        assert not tmp_db.has_url("http://example.com/nope.pdf")

    def test_insert_does_not_mutate_caller_dict(self, tmp_db):
        """insert() copies the record — mutating the original must not affect DB."""
        record = {
            "id": "test1",
            "url": "http://example.com/test1.pdf",
            "published": "2026-01-15T10:00:00Z",
            "title": "Original Title",
            "downloaded": False,
            "summarized": False,
        }
        tmp_db.insert(record)
        record["title"] = "MUTATED"
        assert tmp_db.find()[0]["title"] == "Original Title"

    def test_insert_normalizes_date(self, tmp_db):
        record = {
            "id": "test1",
            "url": "http://example.com/test1.pdf",
            "published": "2025-06-15T15:00:00+03:00",
            "title": "TZ Test",
            "downloaded": False,
            "summarized": False,
        }
        tmp_db.insert(record)
        assert tmp_db.find()[0]["published"] == "2025-06-15T12:00:00Z"


class TestPaperDBFind:

    def _populate(self, db):
        """Insert 3 records spanning Jan-Mar 2026."""
        for month, summ in [(1, False), (2, True), (3, True)]:
            db.insert({
                "id": f"2026-{month:02d}",
                "url": f"http://example.com/{month}.pdf",
                "published": f"2026-{month:02d}-10T00:00:00Z",
                "title": f"Paper {month}",
                "downloaded": True,
                "summarized": summ,
            })

    def test_find_all(self, tmp_db):
        self._populate(tmp_db)
        assert len(tmp_db.find()) == 3

    def test_find_published_gte(self, tmp_db):
        self._populate(tmp_db)
        results = tmp_db.find(published_gte="2026-02-01T00:00:00Z")
        ids = {r["id"] for r in results}
        assert ids == {"2026-02", "2026-03"}

    def test_find_published_gte_boundary(self, tmp_db):
        self._populate(tmp_db)
        # Exact match on the boundary date should be included
        results = tmp_db.find(published_gte="2026-02-10T00:00:00Z")
        ids = {r["id"] for r in results}
        assert "2026-02" in ids

    def test_find_summarized_filter(self, tmp_db):
        self._populate(tmp_db)
        assert len(tmp_db.find(summarized=True)) == 2
        assert len(tmp_db.find(summarized=False)) == 1

    def test_find_combined_filters(self, tmp_db):
        self._populate(tmp_db)
        results = tmp_db.find(published_gte="2026-02-01T00:00:00Z", summarized=True)
        ids = {r["id"] for r in results}
        assert ids == {"2026-02", "2026-03"}

    def test_find_no_matches(self, tmp_db):
        self._populate(tmp_db)
        assert tmp_db.find(published_gte="2027-01-01T00:00:00Z") == []


class TestPaperDBUpdate:

    def test_update_existing(self, tmp_db):
        tmp_db.insert({
            "id": "u1",
            "url": "http://example.com/u1.pdf",
            "published": "2026-01-01T00:00:00Z",
            "title": "U1",
            "downloaded": False,
            "summarized": False,
        })
        result = tmp_db.update("u1", {"downloaded": True, "summarized": True})
        assert result is True
        rec = tmp_db.find()[0]
        assert rec["downloaded"] is True
        assert rec["summarized"] is True

    def test_update_nonexistent(self, tmp_db):
        result = tmp_db.update("nonexistent", {"downloaded": True})
        assert result is False

    def test_update_adds_new_fields(self, tmp_db):
        tmp_db.insert({
            "id": "u2",
            "url": "http://example.com/u2.pdf",
            "published": "2026-01-01T00:00:00Z",
            "title": "U2",
            "downloaded": False,
            "summarized": False,
        })
        tmp_db.update("u2", {"emoji": "🔍", "points": ["a", "b", "c"]})
        rec = tmp_db.find()[0]
        assert rec["emoji"] == "🔍"
        assert rec["points"] == ["a", "b", "c"]


class TestPaperDBResetSummarized:

    def _populate(self, db):
        """Insert papers: one old summarized, one recent summarized, one recent unsummarized."""
        db.insert({
            "id": "old-summ",
            "url": "http://example.com/old.pdf",
            "published": "2025-01-01T00:00:00Z",
            "title": "Old",
            "downloaded": True,
            "summarized": True,
            "emoji": "📄", "tag": "general",
            "one_liner": "Old paper.", "points": ["A"],
            "affiliations": ["MIT"],
            "relevant": True,
        })
        db.insert({
            "id": "new-summ",
            "url": "http://example.com/new.pdf",
            "published": "2026-02-15T00:00:00Z",
            "title": "New Summarized",
            "downloaded": True,
            "summarized": True,
            "emoji": "🛡️", "tag": "security",
            "one_liner": "New paper.", "points": ["B"],
            "affiliations": ["Stanford"],
            "relevant": True,
        })
        db.insert({
            "id": "new-unsumm",
            "url": "http://example.com/new2.pdf",
            "published": "2026-02-15T00:00:00Z",
            "title": "New Unsummarized",
            "downloaded": True,
            "summarized": False,
        })

    def test_resets_only_in_window(self, tmp_db):
        self._populate(tmp_db)
        count = tmp_db.reset_summarized("2026-02-01T00:00:00Z")
        assert count == 1  # only new-summ

        rec = [r for r in tmp_db.find() if r["id"] == "new-summ"][0]
        assert rec["summarized"] is False
        assert "points" not in rec
        assert "one_liner" not in rec
        assert "emoji" not in rec
        assert "affiliations" not in rec
        assert "relevant" not in rec

    def test_leaves_old_records_untouched(self, tmp_db):
        self._populate(tmp_db)
        tmp_db.reset_summarized("2026-02-01T00:00:00Z")

        old = [r for r in tmp_db.find() if r["id"] == "old-summ"][0]
        assert old["summarized"] is True
        assert old["emoji"] == "📄"

    def test_returns_zero_when_nothing_to_reset(self, tmp_db):
        self._populate(tmp_db)
        count = tmp_db.reset_summarized("2027-01-01T00:00:00Z")
        assert count == 0


class TestPaperDBPersistence:

    def test_round_trip(self, tmp_path):
        db_path = str(tmp_path / "persist.json")
        db1 = PaperDB(db_path)
        db1.insert({
            "id": "p1",
            "url": "http://example.com/p1.pdf",
            "published": "2026-01-01T00:00:00Z",
            "title": "Persist Test",
            "downloaded": False,
            "summarized": False,
        })
        db1.update("p1", {"summarized": True, "emoji": "✅"})

        # Re-open from same path
        db2 = PaperDB(db_path)
        assert len(db2.find()) == 1
        rec = db2.find()[0]
        assert rec["summarized"] is True
        assert rec["emoji"] == "✅"

    def test_empty_db_creates_file_on_first_insert(self, tmp_path):
        db_path = tmp_path / "new.json"
        assert not db_path.exists()
        db = PaperDB(str(db_path))
        db.insert({
            "id": "x",
            "url": "http://example.com/x.pdf",
            "published": "2026-01-01T00:00:00Z",
            "title": "X",
            "downloaded": False,
            "summarized": False,
        })
        assert db_path.exists()
        data = json.loads(db_path.read_text())
        assert len(data) == 1
