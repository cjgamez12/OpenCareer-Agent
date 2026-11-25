from dataclasses import dataclass
from datetime import date, datetime

def _clean(text: str | None) -> str:
    """Lowercase and collapse all whitespace; None becomes ""."""
    if text is None:
        return ""
    return " ".join(text.lower().split())

@dataclass(frozen=True)
class JobPosting:
    """One job, in the same shape no matter which API it came from."""

    # Required fields (no default) must come first
    source: str          # which API, e.g. "greenhouse"
    source_id: str       # that API's own ID for the job
    title: str
    company: str
    url: str

    # Optional fields (they have defaults)
    location: str | None = None
    remote: bool = False
    description: str = ""
    posted_at: datetime | None = None
    deadline: date | None = None
    deadline_source: str | None = None   # None = no verified deadline
    tags: tuple[str, ...] = ()

    def fingerprint(self) -> str:
        """Return a key that is the same for duplicate jobs."""
        company = self.company.strip().lower()
        title = self.title.strip().lower()
        location = (self.location or "").strip().lower()
        return "|".join([_clean(self.company), _clean(self.title), _clean(self.location)])
