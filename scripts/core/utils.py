import os
import json
import hashlib
import re
import urllib.request
import time

SEEN_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "seen.json")

# Your profile - LLM uses this to judge relevance
USER_PROFILE = """
- 3rd year B.Tech CSE student (entering 5th semester)
- College: LNCT Bhopal, Madhya Pradesh
- Interests: AI/ML, Software Development, Data Structures & Algorithms, Web Development
- Skills: Python, C++, Java, SQL, HTML, CSS, JavaScript
- Looking for: Software Engineering internships, AI/ML internships, hackathons,
  scholarships, fellowships, research opportunities, coding competitions
- NOT interested in: MBA, law, medical, agriculture, arts/humanities-only roles,
  sales/marketing/HR internships, content writing roles
"""

# ============================================================
# UTILITIES
# ============================================================

SEEN_MAX_AGE = 30 * 86400  # Prune seen entries older than 30 days

# Global error tracker — collects source failures for the Telegram error summary
_source_errors = []

def load_seen():
    """Load previously seen opportunity hashes.

    Supports both old format (list of hashes) and new format
    (dict mapping hash -> unix timestamp). Old format is auto-migrated.
    Returns a dict {hash: timestamp}.
    """
    try:
        with open(SEEN_FILE, "r") as f:
            data = json.load(f)
        if isinstance(data, list):
            # Migrate old list format → dict with current timestamp
            now = int(time.time())
            return {h: now for h in data}
        return data
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_seen(seen_dict):
    """Save seen hashes to file, pruning entries older than SEEN_MAX_AGE."""
    now = int(time.time())
    pruned = {h: ts for h, ts in seen_dict.items() if now - ts < SEEN_MAX_AGE}
    removed = len(seen_dict) - len(pruned)
    if removed:
        print(f"[INFO] Pruned {removed} seen entries older than 30 days")
    os.makedirs(os.path.dirname(SEEN_FILE), exist_ok=True)
    with open(SEEN_FILE, "w") as f:
        json.dump(pruned, f)

def make_hash(text):
    """Create a unique hash for deduplication."""
    return hashlib.md5(text.encode()).hexdigest()

def normalize_key(title):
    """Normalize a title for cross-source dedup.

    The SAME role often appears on multiple sources with tiny differences
    (case, punctuation, 'INTERN' vs 'Intern', extra spaces). We strip all of
    that to a canonical key so duplicates collapse to one.
    """
    t = title.lower()
    t = re.sub(r'[^a-z0-9 ]', ' ', t)      # drop punctuation
    t = re.sub(r'\b(internship|intern|the|a|an|for|at|of|in|to|and)\b', ' ', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t


# Keywords that indicate a listing is NOT an opportunity (exam results, keys, etc.)
JUNK_KEYWORDS = [
    "answer key", "result", "admit card", "hall ticket", "merit list",
    "cut off", "cutoff", "cut-off", "interview schedule", "exam date",
    "exam city", "city slip", "score card", "scorecard", "counselling",
    "counseling", "time table", "timetable", "date sheet", "datesheet",
    "syllabus", "previous year", "exam analysis", "shortlisted candidates",
    "provisional", "revised schedule", "exam pattern", "selection list",
    "document verification", "physical test schedule", "tentative",
]

def is_junk(title):
    """Return True if title is a result/answer-key/admit-card type (not an opportunity)."""
    t = title.lower()
    return any(kw in t for kw in JUNK_KEYWORDS)


# Role types the user is explicitly NOT interested in (hard blocklist - dropped
# before the LLM even sees them, so they can never slip through).
# Multi-word / long phrases: safe to match as substrings.
BLOCKLIST_KEYWORDS = [
    "marketing", "digital marketing", "social media", "human resource",
    "recruitment", "talent acquisition", "business development",
    "video editing", "video editor", "content writ", "copywrit", "copy writer",
    "data entry", "telecalling", "telecaller", "telesales",
    "customer support", "customer service", "customer care",
    "graphic design", "graphics design", "founder office", "founder's office",
    "chief of staff", "brand ambassador", "public relations",
    "fashion", "photography", "videography", "interior design",
    "accounting", "bpo", "influencer", "community manager",
    "campus ambassador", "brand manager", "event management",
    "supply chain", "civil engineering", "mechanical engineer",
    "mbbs", "nursing", "pharmacy", "physiotherapy", "ayurved",
    "chartered accountant", "company secretary", "law clerk",
]
# Short/risky tokens: matched only as whole words (avoids e.g. "Sales" hitting
# "Salesforce", or "ops" hitting "DevOps").
BLOCKLIST_WORDS = ["hr", "bd", "mis", "pr", "ba", "sales", "seo", "accounts", "ops",
                   "ca", "llb", "mbbs"]

def is_blocked(title):
    """Return True if the role is in the user's NOT-interested blocklist."""
    t = " " + re.sub(r'[^a-z0-9 ]', ' ', title.lower()) + " "
    t = re.sub(r'\s+', ' ', t)
    if any(kw in t for kw in BLOCKLIST_KEYWORDS):
        return True
    if any(f" {w} " in t for w in BLOCKLIST_WORDS):
        return True
    return False

def fetch_url(url, headers=None, retries=2, source_name=None):
    """Fetch URL content with retry logic, error tracking, and permissive SSL (for govt sites)."""
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    for attempt in range(1, retries + 1):
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36")
        if headers:
            for k, v in headers.items():
                req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
                return resp.read().decode("utf-8", errors="ignore")
        except Exception as e:
            print(f"[ERROR] Failed to fetch {url} (attempt {attempt}/{retries}): {e}")
            if attempt < retries:
                time.sleep(3)
    # All retries exhausted — track the error for the Telegram summary
    label = source_name or url.split('/')[2]
    _source_errors.append(label)
    return ""

RELEVANT_KEYWORDS = [
    "software", "developer", "comput", "cse", "data scien", "data analy",
    "machine learning", "deep learning", " ai ", "a.i", "artificial intelligence",
    " ml ", "ml ", "nlp", "computer vision", "llm", "python", "java", "c++",
    "web dev", "app dev", "android", "ios", "full stack", "backend", "frontend",
    "programmer", "programming", "coding", "cyber", "security", "cloud",
    "engineer", "engineering", "b.tech", "b.e", "btech", "iot", "robotics",
    "research", "jrf", "technolog", "information technology",
    "embedded", "vlsi", "electronics", "blockchain", "devops", "analytics",
]

# Categories that are always relevant — skip LLM to save quota
AUTO_APPROVE_CATEGORIES = {"HACKATHON", "COMPETITION", "SCHOLARSHIP", "FELLOWSHIP"}

def keyword_relevance(opp):
    """Lightweight relevance check used when the LLM is unavailable."""
    cat = opp["category"]
    if cat in AUTO_APPROVE_CATEGORIES:
        return True
    text = (opp["title"] + " " + opp.get("description", "")).lower()
    return any(kw in text for kw in RELEVANT_KEYWORDS)

