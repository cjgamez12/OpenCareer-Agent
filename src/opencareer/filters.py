import re

from opencareer.models import JobPosting

SENIOR_WORDS = ["senior", "sr", "staff", "lead", "principal", "manager", "director", "head", "vp", "chief"]
EXCLUDE_WORDS = ["assistant", "account executive", "recruiter", "sales", "marketing"]

CATEGORY_KEYWORDS = {
    "cybersecurity": ["security", "soc", "cyber", "penetration"],
    "networking": ["network", "noc"],
    "it": ["it", "help desk", "desktop support", "system administrator"],
    "software": ["software", "developer", "backend", "frontend", "full stack"],
}


def _has_word(text: str, word: str) -> bool:
    """True if `word` appears in `text` as a whole word (not inside another word)."""
    return re.search(rf"\b{re.escape(word)}\b", text.lower()) is not None


def is_entry_level(job: JobPosting) -> bool:
    for word in SENIOR_WORDS:
        if _has_word(job.title, word):
            return False      # found a senior word -> stop right away
    return True               # checked every word, none found


def classify_category(job: JobPosting) -> str | None:
    for word in EXCLUDE_WORDS:
        if _has_word(job.title, word):
            return None
    for category, keywords in CATEGORY_KEYWORDS.items():
        for keyword in keywords:
            if _has_word(job.title, keyword):
                return category
    return None
