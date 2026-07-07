import os
import json
import hashlib
import re
import urllib.request
import time

from core.config import (
    SEEN_FILE, SEEN_MAX_AGE, AUTO_APPROVE_CATEGORIES,
)

# ============================================================
# UTILITIES
# ============================================================

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
    # Non-tech competition / event noise (management, business, arts, media).
    "case competition", "case study competition", "business case",
    "general management", "management track", "strategy case",
    "b-plan", "business plan", "journalism", "wribate", "debate competition",
    "griha decor", "home decor", "memepreneur", "pitch battle",
    "quiz competition", "essay competition", "poster making", "photography contest",
    "short film", "dance", "singing",
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


# ============================================================
# GEO / ELIGIBILITY FILTER
# ============================================================
# The user is an Indian student. Keep opportunities that are in India, remote/
# online/global, or explicitly open to all. Drop roles that are onsite in — or
# restricted to — another country (the biggest source of digest noise, e.g. the
# US/UK/Canada listings from the community GitHub internship repos).

# Positive signals: India locations OR globally-open / remote.
GEO_OK_KEYWORDS = [
    "india", "indian", "bengaluru", "bangalore", "mumbai", "delhi", "new delhi",
    "hyderabad", "pune", "chennai", "kolkata", "noida", "gurgaon", "gurugram",
    "ahmedabad", "jaipur", "indore", "bhopal", "lucknow", "chandigarh", "kochi",
    "coimbatore", "nagpur", "remote", "online", "work from home", "wfh",
    "virtual", "global", "worldwide", "anywhere", "open to all", "international",
]

# "Remote but locked to a foreign country" — still ineligible for the user.
FOREIGN_REMOTE_PATTERNS = [
    "remote in usa", "remote in the us", "remote in united states", "remote, us",
    "remote (us", "us remote", "remote in canada", "remote in uk",
    "remote in the uk", "remote in europe", "remote in germany", "remote in australia",
]

# Foreign country / famous foreign-city signals (matched inside spaced text).
GEO_FOREIGN_KEYWORDS = [
    " usa ", "united states", " u.s.", " uk ", "united kingdom", "canada",
    "canadian", "nigeria", "kenya", "ghana", "south africa", "singapore",
    "germany", " france ", "australia", "netherlands", "ireland", " japan ",
    " china ", " uae ", "dubai", "abu dhabi", " europe ", "spain", "italy",
    "poland", "brazil", "mexico", "israel", "switzerland", "sweden", "denmark",
    "norway", "finland", "portugal", "austria", "belgium", "new zealand",
    "malaysia", "indonesia", "philippines", "vietnam", "thailand", "qatar",
    "saudi",
    # Well-known foreign cities that appear in the GitHub/Simplify feeds.
    "new york", " nyc ", " sf ", "san francisco", " seattle", " boston",
    " austin", " atlanta", " chicago", "mountain view", "palo alto", "san jose",
    "sunnyvale", "redmond", "pasadena", "philadelphia", " miami", "manchester",
    " london", "toronto", "vancouver", "calgary", "ottawa", "montreal", "dublin",
    "berlin", "munich", " paris", "amsterdam", "zurich", "tel aviv", " dallas",
    " houston", " denver", " phoenix", "milpitas", "greenwich", "mclean",
    " reston", "arlington", "charlotte", " raleigh", " durham", "san diego",
    "los angeles", "fort worth", "fort bragg", "state college", "king of prussia",
]

# US state postal codes (uppercase, matched against the ORIGINAL-case text as
# ", CA" / ", NY" — very precise, few false positives).
_US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL",
    "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT",
    "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI",
    "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC",
}
_US_STATE_RE = re.compile(r',\s*([A-Z]{2})\b')


def _has_us_state(raw_text):
    """True if raw_text contains a ', XX' US state postal code (case-sensitive)."""
    return any(m.group(1) in _US_STATES for m in _US_STATE_RE.finditer(raw_text))


def is_geo_ineligible(opp):
    """Return True if the opportunity is onsite in / restricted to a foreign
    country and is NOT open to an Indian student.

    Logic:
      1. If it names India or is clearly remote/online/global/open-to-all → KEEP
         (unless it's "remote in <foreign country>", which is still restricted).
      2. Otherwise, if it names a foreign country/city or a US state code → DROP.
      3. If there is no location signal at all → KEEP (let the LLM decide).
    """
    raw = (opp.get("title", "") + "  " + str(opp.get("description", ""))).strip()
    if not raw:
        return False
    low = " " + raw.lower() + " "

    if any(k in low for k in GEO_OK_KEYWORDS):
        # India / global / remote — but reject foreign-locked "remote in USA" etc.
        if any(p in low for p in FOREIGN_REMOTE_PATTERNS):
            return True
        return False

    if any(k in low for k in GEO_FOREIGN_KEYWORDS):
        return True
    if _has_us_state(raw):
        return True
    return False


# Onsite-job categories where a foreign-only location makes the listing
# inapplicable (a 2028 Indian undergrad can't take an onsite US/UK SWE job).
# Programs the student CAN pursue globally (they're open to relocation) are NOT
# geo-filtered: fellowships, scholarships, competitions, hackathons, research.
GEO_FILTERED_CATEGORIES = {"INTERNSHIP", "GOV JOB"}


def should_drop_geo(opp):
    """Category-gated geo filter.

    Only drop foreign-only *jobs* (internships / govt jobs), which flood the
    digest with roles the student can't realistically take. Fellowships,
    scholarships, competitions and hackathons are kept regardless of location
    because the student is open to relocation and wants global programs — the
    LLM then judges eligibility from the profile.
    """
    if opp.get("category") not in GEO_FILTERED_CATEGORIES:
        return False
    return is_geo_ineligible(opp)

# Realistic browser headers. Many sites (Cloudflare-fronted: SarkariResult,
# OpportunityDesk, HackerEarth, foundit.in) return 403 to a bare/short urllib
# User-Agent, so we present a full, current Chrome fingerprint by default.
DEFAULT_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "application/json;q=0.8,*/*;q=0.7"),
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Ch-Ua": '"Not/A)Brand";v="8", "Chromium";v="126", "Google Chrome";v="126"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Upgrade-Insecure-Requests": "1",
}


def fetch_url(url, headers=None, retries=2, source_name=None):
    """Fetch URL content with retry logic, error tracking, and permissive SSL (for govt sites)."""
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    # Per-call headers override the browser defaults (e.g. Accept: application/json).
    merged_headers = {**DEFAULT_HEADERS, **(headers or {})}

    for attempt in range(1, retries + 1):
        req = urllib.request.Request(url, headers=merged_headers)
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

# AUTO_APPROVE_CATEGORIES is imported from core.config (single source of truth).

def keyword_relevance(opp):
    """Lightweight relevance check used when the LLM is unavailable."""
    cat = opp["category"]
    if cat in AUTO_APPROVE_CATEGORIES:
        return True
    text = (opp["title"] + " " + opp.get("description", "")).lower()
    return any(kw in text for kw in RELEVANT_KEYWORDS)

