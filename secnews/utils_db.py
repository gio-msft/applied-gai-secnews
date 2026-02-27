import json
import logging
import datetime
from pathlib import Path

logger = logging.getLogger("AIRT-GAI-SecNews")


def _normalize_date(iso_str):
    """Normalize an ISO 8601 date string to a consistent UTC format."""
    iso_str = iso_str.replace("Z", "+00:00")
    dt = datetime.datetime.fromisoformat(iso_str)
    dt_utc = dt.astimezone(datetime.timezone.utc)
    return dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")


class PaperDB:
    """Simple JSON-file-backed paper database replacing MongoDB."""

    def __init__(self, path):
        self.path = Path(path)
        if self.path.exists():
            self._data = json.loads(self.path.read_text())
        else:
            self._data = []

    def _save(self):
        self.path.write_text(json.dumps(self._data, indent=2))

    def has_url(self, url):
        """Check if a paper with this URL already exists."""
        return any(r["url"] == url for r in self._data)

    def insert(self, record):
        """Insert a new paper record (normalizes published date)."""
        record = dict(record)
        record["published"] = _normalize_date(record["published"])
        self._data.append(record)
        self._save()

    def update(self, paper_id, fields):
        """Update fields on a paper by its id."""
        for record in self._data:
            if record["id"] == paper_id:
                record.update(fields)
                self._save()
                return True
        return False

    def find(self, published_gte=None, summarized=None):
        """Query papers by optional filters."""
        results = self._data
        if published_gte is not None:
            results = [r for r in results if r["published"] >= published_gte]
        if summarized is not None:
            results = [r for r in results if r.get("summarized") == summarized]
        return results

    def reset_summarized(self, published_gte):
        """Reset summarized papers in the window so they can be re-processed."""
        count = 0
        for record in self._data:
            if record["published"] >= published_gte and record.get("summarized"):
                record["summarized"] = False
                for key in ("points", "one_liner", "emoji", "tag", "affiliations", "relevant", "projects", "interest_score"):
                    record.pop(key, None)
                count += 1
        if count:
            self._save()
        return count
