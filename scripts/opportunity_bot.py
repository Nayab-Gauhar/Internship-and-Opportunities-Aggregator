"""
Unified Opportunity Bot
-----------------------
Fetches opportunities from 20+ sources, classifies relevance using
Groq LLM, and sends matches to Telegram.

SOURCES:
  Govt Jobs    : FreeJobAlert RSS, JagranJosh scrape
  Unstop API   : Scholarships, Internships, Hackathons, Competitions
  Scholarships : ScholarshipsInIndia RSS, ScholarshipRoar RSS
  Hackathons   : HackerEarth API, Devpost API, Codeforces API
  Internships  : GitHub/Simplify, GitHub/speedyapply, foundit.in API, Internshala
  New-Grad     : GitHub/Simplify New-Grad-Positions
  Fellowships  : GovAI (governance.ai)
  Global RSS   : OpportunitiesForYouth, OpportunitiesCircle, OpportunityDesk,
                 OpportunityCell, Oyaop

Requires env vars:
- TELEGRAM_BOT_TOKEN
- TELEGRAM_CHAT_ID
- GROQ_API_KEY
"""

import os
import json
import hashlib
import time
import re
import urllib.request
import urllib.parse
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime

# ============================================================
# CONFIG
# ============================================================

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()

# Groq model - update here if the model gets deprecated (see https://console.groq.com/docs/models)
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.1-70b-versatile").strip()

# Minimum LLM relevance score (0-10) for an opportunity to be sent. Higher = stricter.
MIN_RELEVANCE_SCORE = int(os.environ.get("MIN_RELEVANCE_SCORE", "6"))

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


def send_telegram(message):
    """Send a message to Telegram."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[WARN] Telegram credentials not set. Printing instead:")
        print(message)
        print()
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true"
    }).encode()

    try:
        req = urllib.request.Request(url, data=data)
        urllib.request.urlopen(req, timeout=15)
        time.sleep(1)  # Rate limit: 1 msg/sec
    except Exception as e:
        print(f"[ERROR] Telegram send failed: {e}")


# ============================================================
# SOURCE 1: FreeJobAlert RSS (Government Jobs)
# ============================================================

def fetch_govt_jobs():
    """Fetch latest govt job notifications from FreeJobAlert RSS."""
    print("[INFO] Fetching government jobs from FreeJobAlert RSS...")
    opportunities = []

    xml_content = fetch_url("https://www.freejobalert.com/feed")
    if not xml_content:
        return opportunities

    try:
        root = ET.fromstring(xml_content)
        channel = root.find("channel")
        if channel is None:
            return opportunities

        for item in channel.findall("item")[:25]:
            title = item.findtext("title", "").strip()
            link = item.findtext("link", "").strip()
            desc = item.findtext("description", "").strip()
            pub_date = item.findtext("pubDate", "").strip()

            # Clean HTML from description
            desc = re.sub(r'<[^>]+>', '', desc)[:200]

            if title and link:
                opportunities.append({
                    "source": "FreeJobAlert",
                    "category": "GOV JOB",
                    "title": title,
                    "link": link,
                    "description": desc,
                    "date": pub_date
                })
    except ET.ParseError as e:
        print(f"[ERROR] RSS parse error: {e}")

    print(f"[INFO] Found {len(opportunities)} govt job listings")
    return opportunities


# ============================================================
# SOURCE 2: Unstop API (Scholarships, Internships, Hackathons, Competitions)
# ============================================================

def fetch_unstop(category, label):
    """Fetch opportunities from Unstop public API."""
    print(f"[INFO] Fetching {label} from Unstop...")
    opportunities = []

    url = f"https://unstop.com/api/public/opportunity/search-new?opportunity={category}&per_page=20&oppstatus=open"
    content = fetch_url(url, headers={"Accept": "application/json"})
    if not content:
        return opportunities

    try:
        data = json.loads(content)
        items = data.get("data", {}).get("data", [])

        for item in items:
            title = item.get("title", "").strip()
            public_url = item.get("public_url", "")
            link = f"https://unstop.com/{public_url}" if public_url else ""
            subtype = item.get("subtype", "")
            region = item.get("region", "")
            created = item.get("created_at", "")

            # Get details if available
            details = item.get("details", {})
            desc = ""
            if isinstance(details, dict):
                desc = details.get("short_desc", "") or details.get("description", "")
                desc = re.sub(r'<[^>]+>', '', desc)[:200]

            if title and link:
                opportunities.append({
                    "source": "Unstop",
                    "category": label,
                    "title": title,
                    "link": link,
                    "description": desc or f"Type: {subtype} | Region: {region}",
                    "date": created[:10] if created else ""
                })
    except (json.JSONDecodeError, KeyError) as e:
        print(f"[ERROR] Unstop {category} error: {e}")

    print(f"[INFO] Found {len(opportunities)} {label} listings from Unstop")
    return opportunities


def fetch_unstop_scholarships():
    return fetch_unstop("scholarships", "SCHOLARSHIP")


def fetch_unstop_internships():
    return fetch_unstop("internships", "INTERNSHIP")


def fetch_unstop_hackathons():
    return fetch_unstop("hackathons", "HACKATHON")


def fetch_unstop_competitions():
    return fetch_unstop("competitions", "COMPETITION")


# ============================================================
# SOURCE 3: ScholarshipsInIndia RSS (Scholarships)
# ============================================================

def fetch_scholarshipsinindia():
    """Fetch scholarships from ScholarshipsInIndia.com RSS feed."""
    print("[INFO] Fetching scholarships from ScholarshipsInIndia...")
    opportunities = []

    xml_content = fetch_url("https://www.scholarshipsinindia.com/feed")
    if not xml_content:
        return opportunities

    try:
        root = ET.fromstring(xml_content)
        channel = root.find("channel")
        if channel is None:
            return opportunities

        for item in channel.findall("item")[:15]:
            title = item.findtext("title", "").strip()
            link = item.findtext("link", "").strip()
            desc = item.findtext("description", "").strip()
            pub_date = item.findtext("pubDate", "").strip()

            # Clean HTML from description
            desc = re.sub(r'<[^>]+>', '', desc)[:200]

            if title and link:
                opportunities.append({
                    "source": "ScholarshipsInIndia",
                    "category": "SCHOLARSHIP",
                    "title": title,
                    "link": link,
                    "description": desc,
                    "date": pub_date
                })
    except ET.ParseError as e:
        print(f"[ERROR] ScholarshipsInIndia RSS parse error: {e}")

    print(f"[INFO] Found {len(opportunities)} scholarship listings from ScholarshipsInIndia")
    return opportunities


# ============================================================
# SOURCE 4: JagranJosh (Government Jobs / Exam News - HTML scrape)
# ============================================================

def fetch_jagranjosh():
    """Scrape latest govt job / recruitment listings from JagranJosh jobs page."""
    print("[INFO] Fetching govt jobs from JagranJosh...")
    opportunities = []

    html = fetch_url("https://www.jagranjosh.com/jobs")
    if not html:
        return opportunities

    # Extract article links with their titles
    pattern = re.findall(
        r'href="(https://www\.jagranjosh\.com/articles/[a-z0-9\-]+)"[^>]*>([^<]{15,100})',
        html
    )

    seen_links = set()
    # Keywords that indicate an actual job/recruitment (filter out result/admit-card noise)
    job_keywords = ["recruitment", "notification", "vacancy", "apply", "bharti",
                    "posts", "form", "hiring", "jobs"]

    for link, title in pattern:
        if link in seen_links:
            continue
        seen_links.add(link)

        title = title.replace("&amp;", "&").strip()
        title_lower = title.lower()

        # Only keep recruitment/job-type articles
        if any(kw in title_lower or kw in link.lower() for kw in job_keywords):
            opportunities.append({
                "source": "JagranJosh",
                "category": "GOV JOB",
                "title": title,
                "link": link,
                "description": "",
                "date": ""
            })

    print(f"[INFO] Found {len(opportunities)} govt job listings from JagranJosh")
    return opportunities


# ============================================================
# GENERIC RSS FETCHER (for global opportunity aggregators)
# ============================================================

def detect_category(title, categories):
    """Infer opportunity category from title + RSS category tags."""
    text = (title + " " + " ".join(categories)).lower()
    # Order matters - most specific first
    if any(k in text for k in ["fellowship", "fellow "]):
        return "FELLOWSHIP"
    if any(k in text for k in ["scholarship", "scholar ", "study in", "masters", "phd scholar"]):
        return "SCHOLARSHIP"
    if any(k in text for k in ["internship", "intern "]):
        return "INTERNSHIP"
    if any(k in text for k in ["hackathon"]):
        return "HACKATHON"
    if any(k in text for k in ["competition", "contest", "award", "challenge", "prize"]):
        return "COMPETITION"
    if any(k in text for k in ["job", "recruitment", "vacancy", "consultant", "career"]):
        return "GOV JOB"
    return "OPPORTUNITY"


def fetch_generic_rss(url, source_name, limit=12):
    """Fetch and categorize opportunities from a generic RSS feed."""
    print(f"[INFO] Fetching opportunities from {source_name}...")
    opportunities = []

    xml_content = fetch_url(url)
    if not xml_content:
        return opportunities

    try:
        root = ET.fromstring(xml_content)
        channel = root.find("channel")
        if channel is None:
            return opportunities

        for item in channel.findall("item")[:limit]:
            title = item.findtext("title", "").strip()
            link = item.findtext("link", "").strip()
            desc = item.findtext("description", "").strip()
            pub_date = item.findtext("pubDate", "").strip()
            categories = [c.text for c in item.findall("category") if c.text]

            desc = re.sub(r'<[^>]+>', '', desc)[:180]

            if title and link:
                opportunities.append({
                    "source": source_name,
                    "category": detect_category(title, categories),
                    "title": title,
                    "link": link,
                    "description": desc,
                    "date": pub_date
                })
    except ET.ParseError as e:
        print(f"[ERROR] {source_name} RSS parse error: {e}")

    print(f"[INFO] Found {len(opportunities)} listings from {source_name}")
    return opportunities


# ============================================================
# SOURCE 6: HackerEarth (Hackathons + Hiring Challenges)
# ============================================================

def fetch_hackerearth():
    """Fetch hackathons and hiring challenges from HackerEarth events API."""
    print("[INFO] Fetching hackathons from HackerEarth...")
    opportunities = []

    content = fetch_url(
        "https://www.hackerearth.com/chrome-extension/events/",
        headers={
            "Accept": "application/json",
            "Referer": "https://www.hackerearth.com/challenges/",
            "Accept-Language": "en-US,en;q=0.9",
            "X-Requested-With": "XMLHttpRequest",
        }
    )
    if not content:
        return opportunities

    try:
        data = json.loads(content)
        events = data.get("response", [])

        for e in events:
            title = e.get("title", "").strip()
            link = e.get("url", "").strip()
            end = e.get("end_tz", "") or e.get("end", "")

            if not (title and link):
                continue

            # Categorize by URL pattern
            low = link.lower()
            if "hiring" in low or "competitive" in low:
                category = "COMPETITION"
            else:
                category = "HACKATHON"

            opportunities.append({
                "source": "HackerEarth",
                "category": category,
                "title": title,
                "link": link,
                "description": "",
                "date": end[:10] if end else ""
            })
    except json.JSONDecodeError as e:
        print(f"[ERROR] HackerEarth JSON error: {e}")

    print(f"[INFO] Found {len(opportunities)} listings from HackerEarth")
    return opportunities


# ============================================================
# SOURCE 7: Devpost (International Hackathons)
# ============================================================

def fetch_devpost_hackathons():
    """Fetch upcoming hackathons from Devpost API."""
    print("[INFO] Fetching international hackathons from Devpost...")
    opportunities = []

    url = "https://devpost.com/api/hackathons?status[]=upcoming&status[]=open"
    content = fetch_url(url, headers={"Accept": "application/json"})
    if not content:
        return opportunities

    try:
        data = json.loads(content)
        hackathons = data.get("hackathons", [])

        for h in hackathons[:15]:
            title = h.get("title", "").strip()
            link = h.get("url", "").strip()
            desc = h.get("tagline", "") or ""
            deadline = h.get("submission_period_dates", "")
            prizes = h.get("prize_amount", "")

            if title and link:
                description = desc[:150]
                if prizes:
                    description += f" | Prize: {prizes}"

                opportunities.append({
                    "source": "Devpost",
                    "category": "HACKATHON",
                    "title": title,
                    "link": link,
                    "description": description,
                    "date": deadline
                })
    except json.JSONDecodeError as e:
        print(f"[ERROR] Devpost JSON error: {e}")

    print(f"[INFO] Found {len(opportunities)} hackathon listings from Devpost")
    return opportunities


# ============================================================
# SOURCE: Community GitHub repos (structured JSON, no scraping/Cloudflare)
# ============================================================

def fetch_github_internships():
    """Fetch internships from community-maintained GitHub repos (Simplify listings).

    These repos store a clean listings.json that is updated continuously via PRs.
    Fetched from raw.githubusercontent.com (no JS, no Cloudflare). We only keep
    recently-updated, active roles and drop US-citizenship-only ones.
    """
    print("[INFO] Fetching internships from community GitHub repos...")
    opportunities = []

    repos = [
        ("SimplifyJobs/Summer2026-Internships",
         "https://raw.githubusercontent.com/SimplifyJobs/Summer2026-Internships/dev/.github/scripts/listings.json"),
    ]

    recent_window = 5 * 86400  # only roles updated in the last 5 days
    now = time.time()

    for repo_name, url in repos:
        content = fetch_url(url)
        if not content:
            continue
        try:
            listings = json.loads(content)
        except json.JSONDecodeError as e:
            print(f"[ERROR] {repo_name} JSON error: {e}")
            continue

        count = 0
        for item in listings:
            if not item.get("active", False):
                continue
            # Skip roles that require US citizenship (not applicable to the user)
            if item.get("sponsorship", "") == "U.S. Citizenship is Required":
                continue
            # Only recently updated roles (dedup + recency keeps volume sane)
            updated = item.get("date_updated", 0)
            if now - updated > recent_window:
                continue

            company = (item.get("company_name") or "").strip()
            role = (item.get("title") or "").strip()
            url_apply = (item.get("url") or "").strip()
            if not (company and role and url_apply):
                continue

            locations = item.get("locations") or []
            loc = ", ".join(locations[:2]) if locations else ""
            cat = item.get("category", "")

            opportunities.append({
                "source": "GitHub/Simplify",
                "category": "INTERNSHIP",
                "title": f"{role} @ {company}",
                "link": url_apply,
                "description": f"{cat} | {loc}" if loc else cat,
                "date": datetime.fromtimestamp(updated).strftime("%Y-%m-%d") if updated else ""
            })
            count += 1

        print(f"[INFO] Found {count} recent internships from {repo_name}")

    return opportunities


def fetch_speedyapply_intl():
    """Parse speedyapply's international internships markdown table.

    speedyapply/2026-SWE-College-Jobs publishes INTERN_INTL.md (a Markdown table)
    with non-US internships, including many in India. We parse the table and keep
    only recently-posted roles (age in hours, or <= 5 days).
    """
    print("[INFO] Fetching international internships from speedyapply...")
    opportunities = []

    md = fetch_url("https://raw.githubusercontent.com/speedyapply/2026-SWE-College-Jobs/main/INTERN_INTL.md")
    if not md:
        return opportunities

    def strip_tags(s):
        return re.sub(r'<[^>]+>', '', s).replace('&amp;', '&').strip()

    recent_ages = {"0d", "1d", "2d", "3d", "4d", "5d"}
    rows = [l for l in md.splitlines() if l.startswith("|")]
    # skip header row + separator row
    for row in rows[2:]:
        cols = [c.strip() for c in row.split("|")[1:-1]]
        if len(cols) < 5:
            continue
        company = strip_tags(cols[0])
        position = strip_tags(cols[1])
        location = strip_tags(cols[2])
        m = re.search(r'href="([^"]+)"', cols[3])
        link = m.group(1) if m else ""
        age = strip_tags(cols[4])

        # Keep only recent (hours, or within 5 days)
        if not (("h" in age) or (age in recent_ages)):
            continue
        if not (company and position and link):
            continue

        opportunities.append({
            "source": "GitHub/speedyapply",
            "category": "INTERNSHIP",
            "title": f"{position} @ {company}",
            "link": link,
            "description": location,
            "date": age + " ago" if age else ""
        })

    print(f"[INFO] Found {len(opportunities)} recent intl internships from speedyapply")
    return opportunities


# ============================================================
# SOURCE: foundit.in (internships via JSON search API)
# ============================================================

def fetch_foundit():
    """Fetch internships from foundit.in's job search API (JSON).

    foundit (formerly Monster India) exposes a middleware search API that returns
    clean JSON when called with browser headers. We query several tech terms and
    dedupe; recency is handled by the global seen.json dedup.
    """
    print("[INFO] Fetching internships from foundit.in...")
    opportunities = []

    queries = [
        "software developer intern",
        "machine learning intern",
        "data science intern",
        "AI intern",
        "backend developer intern",
    ]
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "identity",
        "Referer": "https://www.foundit.in/search/jobs",
        "Origin": "https://www.foundit.in",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Ch-Ua": '"Chromium";v="120", "Google Chrome";v="120", "Not?A_Brand";v="99"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Linux"',
        "Connection": "keep-alive",
    }

    seen_ids = set()
    for q in queries:
        url = ("https://www.foundit.in/middleware/jobsearch?start=0&limit=12&query="
               + urllib.parse.quote(q))
        content = fetch_url(url, headers=headers)
        if not content:
            continue
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            continue

        for j in data.get("jobSearchResponse", {}).get("data", []):
            jid = j.get("jobId")
            if jid in seen_ids:
                continue
            seen_ids.add(jid)

            title = (j.get("title") or "").strip()
            company = (j.get("companyName") or "").strip()
            locations = (j.get("locations") or "").strip()
            seo = j.get("seoJdUrl") or ""
            link = ("https://www.foundit.in" + seo) if seo else (j.get("redirectUrl") or "")
            if not (title and link):
                continue

            opportunities.append({
                "source": "foundit.in",
                "category": "INTERNSHIP",
                "title": f"{title} @ {company}" if company else title,
                "link": link,
                "description": locations,
                "date": ""
            })

    print(f"[INFO] Found {len(opportunities)} internships from foundit.in")
    return opportunities


# ============================================================
# SOURCE: GovAI (governance.ai) - AI governance fellowships
# ============================================================

def fetch_governance_ai():
    """Scrape GovAI's opportunities page (AI governance fellowships/programs).

    The /opportunities page is server-rendered with <a href="/post/..."> links;
    we derive each title from the heading that precedes its 'Read more' link.
    Small but high-value (AI policy/research fellowships).
    """
    print("[INFO] Fetching AI fellowships from GovAI (governance.ai)...")
    opportunities = []

    html = fetch_url("https://www.governance.ai/opportunities")
    if not html:
        return opportunities

    def clean(t):
        return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', t)).replace('&amp;', '&').strip()

    seen_links = set()
    for m in re.finditer(r'href="(/post/[a-z0-9\-]+)"', html):
        href = m.group(1)
        if href in seen_links:
            continue
        seen_links.add(href)

        before = html[:m.start()]
        heads = re.findall(r'<h[1-4][^>]*>(.*?)</h[1-4]>', before, re.S)
        title = clean(heads[-1]) if heads else href.split("/post/")[1].replace("-", " ").title()
        if not title:
            continue

        opportunities.append({
            "source": "GovAI",
            "category": "FELLOWSHIP",
            "title": title,
            "link": "https://www.governance.ai" + href,
            "description": "AI governance / policy / research",
            "date": ""
        })

    print(f"[INFO] Found {len(opportunities)} AI fellowships from GovAI")
    return opportunities


# ============================================================
# SOURCE: Codeforces (upcoming coding contests)
# ============================================================

def fetch_codeforces():
    """Fetch upcoming contests from the Codeforces public API."""
    print("[INFO] Fetching upcoming contests from Codeforces...")
    opportunities = []

    content = fetch_url(
        "https://codeforces.com/api/contest.list?gym=false",
        source_name="Codeforces"
    )
    if not content:
        return opportunities

    try:
        data = json.loads(content)
        if data.get("status") != "OK":
            return opportunities

        for c in data.get("result", []):
            if c.get("phase") != "BEFORE":
                continue
            name = c.get("name", "").strip()
            cid = c.get("id", "")
            start = c.get("startTimeSeconds", 0)

            if not name:
                continue

            link = f"https://codeforces.com/contest/{cid}"
            start_dt = datetime.fromtimestamp(start).strftime("%Y-%m-%d %H:%M") if start else ""

            opportunities.append({
                "source": "Codeforces",
                "category": "COMPETITION",
                "title": name,
                "link": link,
                "description": f"Starts: {start_dt}" if start_dt else "",
                "date": start_dt
            })
    except json.JSONDecodeError as e:
        print(f"[ERROR] Codeforces JSON error: {e}")

    print(f"[INFO] Found {len(opportunities)} upcoming contests from Codeforces")
    return opportunities


# ============================================================
# SOURCE: Internshala (India internships - HTML scrape)
# ============================================================

def fetch_internshala():
    """Fetch tech internship listings from Internshala via JSON-LD structured data."""
    print("[INFO] Fetching internships from Internshala...")
    opportunities = []

    pages = [
        ("computer-science-internship", "CS"),
        ("machine-learning-internship", "ML"),
        ("python-django-internship", "Python/Django"),
        ("web-development-internship", "Web Dev"),
    ]

    seen_links = set()
    for slug, tag in pages:
        url = f"https://internshala.com/internships/{slug}"
        html = fetch_url(url, source_name="Internshala")
        if not html:
            continue

        # Extract JSON-LD structured data (schema.org ItemList)
        for m in re.finditer(
            r'<script type="application/ld\+json">(.*?)</script>', html, re.S
        ):
            try:
                data = json.loads(m.group(1))
            except json.JSONDecodeError:
                continue
            if data.get("@type") != "ItemList":
                continue

            for item in data.get("itemListElement", [])[:20]:
                title = item.get("name", "").strip()
                link = item.get("url", "").strip()
                if not (title and link) or link in seen_links:
                    continue
                seen_links.add(link)

                opportunities.append({
                    "source": "Internshala",
                    "category": "INTERNSHIP",
                    "title": title,
                    "link": link,
                    "description": tag,
                    "date": ""
                })
            break  # Only need the first ItemList

        time.sleep(1)  # Polite crawling

    print(f"[INFO] Found {len(opportunities)} internships from Internshala")
    return opportunities


# ============================================================
# SOURCE: Simplify New-Grad Positions (GitHub JSON)
# ============================================================

def fetch_github_newgrad():
    """Fetch new-grad SWE positions from Simplify's community GitHub repo."""
    print("[INFO] Fetching new-grad positions from GitHub/Simplify...")
    opportunities = []

    url = ("https://raw.githubusercontent.com/SimplifyJobs/"
           "New-Grad-Positions/dev/.github/scripts/listings.json")
    content = fetch_url(url, source_name="GitHub/Simplify-NewGrad")
    if not content:
        return opportunities

    recent_window = 5 * 86400
    now = time.time()

    try:
        listings = json.loads(content)
    except json.JSONDecodeError as e:
        print(f"[ERROR] Simplify New-Grad JSON error: {e}")
        return opportunities

    for item in listings:
        if not item.get("active", False):
            continue
        if item.get("sponsorship", "") == "U.S. Citizenship is Required":
            continue
        updated = item.get("date_updated", 0)
        if now - updated > recent_window:
            continue

        company = (item.get("company_name") or "").strip()
        role = (item.get("title") or "").strip()
        url_apply = (item.get("url") or "").strip()
        if not (company and role and url_apply):
            continue

        locations = item.get("locations") or []
        loc = ", ".join(locations[:2]) if locations else ""

        opportunities.append({
            "source": "GitHub/Simplify-NewGrad",
            "category": "INTERNSHIP",
            "title": f"{role} @ {company}",
            "link": url_apply,
            "description": f"New Grad | {loc}" if loc else "New Grad",
            "date": datetime.fromtimestamp(updated).strftime("%Y-%m-%d") if updated else ""
        })

    print(f"[INFO] Found {len(opportunities)} new-grad positions from Simplify")
    return opportunities


# ============================================================
# SOURCE: Sarkari Result (Government Jobs)
# ============================================================

def fetch_sarkari_result():
    """Scrape the latest jobs from Sarkari Result."""
    print("[INFO] Fetching latest jobs from Sarkari Result...")
    opportunities = []

    html = fetch_url("https://www.sarkariresult.com/", source_name="SarkariResult")
    if not html:
        return opportunities

    # Look for links in the "Latest Jobs" box, which typically have a year in the path
    links = re.findall(r'<a href="(https://www\.sarkariresult\.com/\d+/[^"]+)"[^>]*>(.*?)</a>', html)
    if not links:
        # Fallback to category links
        links = re.findall(r'<a href="(https://www\.sarkariresult\.com/[a-z]+/[^"]+)"[^>]*>(.*?)</a>', html)

    seen = set()
    for link, title in links:
        title = re.sub(r'<[^>]+>', '', title).strip()
        if not title or link in seen:
            continue
        seen.add(link)
        
        # Stop collecting after we get a reasonable batch of recent jobs
        if len(opportunities) >= 20:
            break

        opportunities.append({
            "source": "SarkariResult",
            "category": "GOVT_JOB",
            "title": title,
            "link": link,
            "description": "Sarkari Result Latest Job",
            "date": ""
        })

    print(f"[INFO] Found {len(opportunities)} jobs from Sarkari Result")
    return opportunities


# ============================================================
# SOURCE: Devfolio (Community Hackathons)
# ============================================================

def fetch_devfolio():
    """Scrape upcoming hackathons from Devfolio."""
    print("[INFO] Fetching hackathons from Devfolio...")
    opportunities = []

    html = fetch_url("https://devfolio.co/hackathons", source_name="Devfolio")
    if not html:
        return opportunities

    # Devfolio slugs look like href="https://<slug>.devfolio.co/"
    slugs = re.findall(r'href="https://([a-z0-9\-]+)\.devfolio\.co/?"', html)
    
    seen = set()
    for slug in slugs:
        if slug in seen:
            continue
        seen.add(slug)
        
        # Filter out common false-positives
        if slug in ("www", "api", "blog", "sponsor"):
            continue

        opportunities.append({
            "source": "Devfolio",
            "category": "HACKATHON",
            "title": f"Devfolio: {slug.replace('-', ' ').title()}",
            "link": f"https://{slug}.devfolio.co/",
            "description": "Devfolio Hackathon",
            "date": ""
        })

    print(f"[INFO] Found {len(opportunities)} hackathons from Devfolio")
    return opportunities


# ============================================================
# SOURCE: AICTE Internship Portal
# ============================================================

def fetch_aicte_internships():
    """Scrape the AICTE internship portal for tech/AI opportunities."""
    print("[INFO] Fetching internships from AICTE...")
    opportunities = []

    html = fetch_url("https://internship.aicte-india.org/", source_name="AICTE")
    if not html:
        return opportunities

    # Very naive scrape since the portal is heavily dynamic, we just grab links that have "internship"
    links = re.findall(r'href="(https://internship\.aicte-india\.org/[^"]+)"', html)
    
    seen = set()
    for link in links:
        if "internship" not in link.lower() or link in seen:
            continue
        seen.add(link)

        opportunities.append({
            "source": "AICTE",
            "category": "INTERNSHIP",
            "title": "AICTE Internship Opportunity",
            "link": link,
            "description": "Check the AICTE portal for details.",
            "date": ""
        })
        
        if len(opportunities) >= 5: # Just grab a few top ones
            break

    print(f"[INFO] Found {len(opportunities)} internship links from AICTE")
    return opportunities


# ============================================================
# SOURCE: ISTI Portal (Fellowships)
# ============================================================

def fetch_isti_portal():
    """Scrape the ISTI portal for fellowships and research funding."""
    print("[INFO] Fetching fellowships from ISTI Portal...")
    opportunities = []

    html = fetch_url("https://www.indiascienceandtechnology.gov.in/listingpage/internships", source_name="ISTI")
    if not html:
        return opportunities

    # Extract Drupal node links (typical for ISTI)
    titles = re.findall(r'<span class="field-content"><a href="(/node/\d+)">([^<]+)</a></span>', html)
    if not titles:
        titles = re.findall(r'<a href="(/node/\d+)">([^<]+)</a>', html)
        
    seen = set()
    for link, title in titles:
        if link in seen or not title.strip():
            continue
        seen.add(link)

        opportunities.append({
            "source": "ISTI",
            "category": "FELLOWSHIP",
            "title": title.strip(),
            "link": f"https://www.indiascienceandtechnology.gov.in{link}",
            "description": "ISTI Fellowship/Internship",
            "date": ""
        })

    print(f"[INFO] Found {len(opportunities)} fellowships from ISTI")
    return opportunities


# ============================================================
# SOURCE: MyGov (Campaigns / Competitions)
# ============================================================

def fetch_mygov():
    """Scrape MyGov for active competitions, quizzes, and tasks."""
    print("[INFO] Fetching competitions from MyGov...")
    opportunities = []

    html = fetch_url("https://www.mygov.in/", source_name="MyGov")
    if not html:
        return opportunities

    # Find links on the page that belong to innovateindia, quiz, or task
    links = re.findall(r'<a href="(https://(?:innovateindia|quiz|task)\.mygov\.in/[^"]+)"[^>]*>(.*?)</a>', html)
    
    seen = set()
    for link, title in links:
        title = re.sub(r'<[^>]+>', '', title).strip()
        if link in seen or not title:
            continue
        seen.add(link)

        opportunities.append({
            "source": "MyGov",
            "category": "COMPETITION",
            "title": f"MyGov: {title}",
            "link": link,
            "description": "Govt of India Campaign/Competition",
            "date": ""
        })

    print(f"[INFO] Found {len(opportunities)} competitions from MyGov")
    return opportunities


# ============================================================
# LLM CLASSIFICATION (Groq - free tier, llama-3.1-8b-instant)
# ============================================================

# Keywords indicating tech/CS/research relevance (used as fallback when LLM is down)
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


def keyword_relevance(opp):
    """Lightweight relevance check used when the LLM is unavailable.

    - Scholarships / fellowships / hackathons / competitions: always kept (broadly useful)
    - Internships / jobs: kept only if tech/CS/engineering keywords match
    """
    cat = opp["category"]
    if cat in ("SCHOLARSHIP", "FELLOWSHIP", "HACKATHON", "COMPETITION"):
        return True
    text = (opp["title"] + " " + opp.get("description", "")).lower()
    return any(kw in text for kw in RELEVANT_KEYWORDS)


# Categories that are always relevant — skip LLM to save quota
AUTO_APPROVE_CATEGORIES = {"HACKATHON", "COMPETITION", "SCHOLARSHIP", "FELLOWSHIP"}


def classify_with_llm(opportunities):
    """Score each opportunity 0-10 for relevance to the user, keep those scoring
    >= MIN_RELEVANCE_SCORE, and return them sorted best-first (with `_score` set).

    Categories in AUTO_APPROVE_CATEGORIES skip the LLM entirely (auto-score 7).
    Falls back to keyword_relevance() when the LLM is unavailable or errors out.
    """
    if not opportunities:
        return []

    # --- Auto-approve obvious categories without burning LLM calls ---
    auto_kept = []
    needs_llm = []
    for opp in opportunities:
        if opp["category"] in AUTO_APPROVE_CATEGORIES:
            opp["_score"] = 7
            auto_kept.append(opp)
        else:
            needs_llm.append(opp)

    if auto_kept:
        print(f"[INFO] Auto-approved {len(auto_kept)} hackathons/competitions/"
              f"scholarships/fellowships (skipped LLM)")

    if not GROQ_API_KEY:
        print("[WARN] No GROQ_API_KEY set. Using keyword fallback filter.")
        kept = [o for o in needs_llm if keyword_relevance(o)]
        for o in kept:
            o["_score"] = 0
        return auto_kept + kept

    print(f"[INFO] Scoring {len(needs_llm)} opportunities with Groq LLM "
          f"(threshold {MIN_RELEVANCE_SCORE}/10)...")


    relevant = []
    batch_size = 15

    for i in range(0, len(needs_llm), batch_size):
        batch = needs_llm[i:i + batch_size]

        listings_text = ""
        for idx, opp in enumerate(batch):
            listings_text += f"\n{idx+1}. [{opp['category']}] {opp['title']}"
            if opp['description']:
                listings_text += f" - {opp['description'][:100]}"

        prompt = f"""You are a career opportunity matcher for an Indian engineering student.
Score EACH opportunity from 0 to 10 for how relevant it is to THIS student.

STUDENT PROFILE:
{USER_PROFILE}

OPPORTUNITIES:
{listings_text}

SCORING GUIDE:
- 9-10: Perfect fit (AI/ML, software, data science, CS research, tech fellowship/scholarship matching their skills)
- 6-8: Good fit (general software/engineering/tech role, coding hackathon, eligible engineering scholarship)
- 3-5: Weak/uncertain fit (tangentially technical, or eligibility unclear)
- 0-2: Not relevant (sales, marketing, HR, content/video, non-tech, MBA/medical/law, needs PhD/PG, ineligible)

Respond with ONLY a JSON object mapping each opportunity number to its score:
{{"scores": {{"1": 9, "2": 2, "3": 7}}}}
Include every number from the list."""

        url = "https://api.groq.com/openai/v1/chat/completions"
        payload = json.dumps({
            "model": GROQ_MODEL,
            "messages": [
                {"role": "system", "content": "You are a precise scorer. You respond ONLY with valid JSON, no explanations."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.0,
            "max_tokens": 300,
            "response_format": {"type": "json_object"}
        })

        req = urllib.request.Request(url, data=payload.encode())
        req.add_header("Authorization", f"Bearer {GROQ_API_KEY}")
        req.add_header("Content-Type", "application/json")
        # Groq's API is behind Cloudflare, which blocks the default Python urllib
        # User-Agent (causes "403 error code: 1010"). A browser UA avoids the block.
        req.add_header("User-Agent", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode())
                answer = result["choices"][0]["message"]["content"].strip()
                print(f"[INFO] LLM batch {i//batch_size + 1}: {answer[:140]}")

                scores = {}
                try:
                    scores = json.loads(answer).get("scores", {})
                except (json.JSONDecodeError, AttributeError):
                    # Fallback: pull "num": score pairs out of stray text
                    for n, s in re.findall(r'"(\d+)"\s*:\s*(\d+)', answer):
                        scores[n] = int(s)

                for idx, opp in enumerate(batch):
                    try:
                        sc = int(scores.get(str(idx + 1), 0))
                    except (ValueError, TypeError):
                        sc = 0
                    if sc >= MIN_RELEVANCE_SCORE:
                        opp["_score"] = sc
                        relevant.append(opp)

        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode()[:300]
            except Exception:
                pass
            print(f"[ERROR] Groq HTTP {e.code}: {body}")
            if e.code in (401, 403):
                print("[HINT] Check your GROQ_API_KEY secret is valid & active. "
                      f"If the model '{GROQ_MODEL}' is deprecated, set a GROQ_MODEL secret "
                      "to a current model from https://console.groq.com/docs/models")
            # Fall back to keyword filter for this batch (don't flood)
            for o in batch:
                if keyword_relevance(o):
                    o["_score"] = 0
                    relevant.append(o)
        except Exception as e:
            print(f"[ERROR] Groq API error: {e}")
            for o in batch:
                if keyword_relevance(o):
                    o["_score"] = 0
                    relevant.append(o)

        time.sleep(2)  # Rate limiting between batches (free tier is strict)

    # Merge auto-approved + LLM-approved, sort best-first
    all_relevant = auto_kept + relevant
    all_relevant.sort(key=lambda o: o.get("_score", 0), reverse=True)
    print(f"[INFO] LLM kept {len(relevant)} of {len(needs_llm)} "
          f"(score >= {MIN_RELEVANCE_SCORE}), "
          f"+ {len(auto_kept)} auto-approved = {len(all_relevant)} total")
    return all_relevant


# ============================================================
# MAIN
# ============================================================

CATEGORY_META = {
    "INTERNSHIP":  ("\U0001f4bc", "Internships"),
    "HACKATHON":   ("\U0001f680", "Hackathons"),
    "COMPETITION": ("\U0001f3c6", "Competitions"),
    "FELLOWSHIP":  ("\U0001f52c", "Fellowships"),
    "SCHOLARSHIP": ("\U0001f393", "Scholarships"),
    "GOV JOB":     ("\U0001f3db\ufe0f", "Government Jobs"),
    "OPPORTUNITY": ("\U0001f4cc", "Other Opportunities"),
}
# Order in which categories appear in the digest (most relevant first)
CATEGORY_ORDER = ["INTERNSHIP", "HACKATHON", "COMPETITION", "FELLOWSHIP",
                  "SCHOLARSHIP", "GOV JOB", "OPPORTUNITY"]

MAX_MSG_CHARS = 3500   # safe budget under Telegram's 4096-char per-message limit


def esc(text):
    """Escape HTML special chars for Telegram HTML parse mode."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_item(index, opp):
    """Format a single opportunity as a COMPACT one-line Telegram entry.

    Layout:  N. <a href=link>Title</a> · meta · source
    (meta = location/date if present). Keeps messages small so more items fit.
    """
    title = esc(opp["title"][:95])
    # Highlight top matches (LLM score 9-10) with a star
    star = "\u2b50 " if opp.get("_score", 0) >= 9 else ""
    line = f"{index}. {star}<a href=\"{opp['link']}\">{title}</a>"

    meta = opp.get("description") or opp.get("date") or ""
    meta = esc(str(meta).strip()[:35])
    if meta:
        line += f" \u00b7 {meta}"
    line += f" \u00b7 <i>{esc(opp['source'])}</i>\n"
    return line


def send_category(category, items):
    """Send ALL items of a category to Telegram, splitting across multiple
    messages when needed to stay under Telegram's per-message size limit."""
    emoji, label = CATEGORY_META.get(category, ("\U0001f4cc", category.title()))
    total = len(items)

    header = f"{emoji} <b>{label.upper()}</b>  ({total} new)\n" + "\u2501" * 18 + "\n"
    msg = header
    part = 1

    for i, opp in enumerate(items, 1):
        block = format_item(i, opp)
        # If adding this block would exceed the budget, flush current message first
        if len(msg) + len(block) > MAX_MSG_CHARS:
            send_telegram(msg)
            part += 1
            msg = f"{emoji} <b>{label.upper()}</b>  (contd. {part})\n" + "\u2501" * 18 + "\n" + block
        else:
            msg += block

    if msg.strip():
        send_telegram(msg)


def send_digest(relevant, total_new, total_fetched=0):
    """Group opportunities by category and send a clean digest to Telegram."""
    grouped = {}
    for opp in relevant:
        grouped.setdefault(opp["category"], []).append(opp)

    # ---- Summary header with per-category breakdown ----
    header = "\U0001f514 <b>New Opportunities for You!</b>\n"
    header += f"\U0001f4c6 <i>{datetime.now().strftime('%d %b %Y, %I:%M %p')}</i>\n\n"
    for cat in CATEGORY_ORDER:
        if cat in grouped:
            emoji, label = CATEGORY_META[cat]
            header += f"{emoji} {label}: <b>{len(grouped[cat])}</b>\n"
    header += f"\n\U0001f4ca <b>{len(relevant)}</b> relevant out of {total_new} new"
    send_telegram(header)

    # ---- All items per category (auto-split into multiple messages) ----
    for cat in CATEGORY_ORDER:
        if cat in grouped:
            send_category(cat, grouped[cat])

    # ---- Stats footer ----
    footer_parts = [f"\U0001f4ca Fetched: {total_fetched}",
                    f"New: {total_new}",
                    f"Sent: {len(relevant)}"]
    if _source_errors:
        unique_errors = list(dict.fromkeys(_source_errors))  # dedupe, preserve order
        footer_parts.append(f"\n\u26a0\ufe0f Failed: {', '.join(unique_errors[:5])}")
    send_telegram(" \u00b7 ".join(footer_parts))


def send_monthly_reminders():
    """Send a monthly static reminder for decentralized / unscrapeable portals."""
    # Only run on the 1st of the month
    if datetime.now().day != 1:
        return

    msg = (
        "\U0001f4c2 <b>Monthly Manual Check Reminder</b>\n\n"
        "Some portals are decentralized, require logins, or block automated bots. Please check these manually:\n\n"
        "• <b>DRDO</b>: Check CAIR/DYSL-AI lab sites or email them\n"
        "• <b>ISRO</b>: Check SAC, NRSC, and IIRS portals\n"
        "• <b>myScheme & NSP</b>: Check for new state/central scholarships\n"
        "• <b>PM Internship</b>: pminternship.mca.gov.in\n"
        "• <b>NITI Aayog</b>: workforindia.niti.gov.in\n"
        "• <b>IndiaAI</b>: fellowship.indiaai.gov.in\n"
        "• <b>iDEX</b>: Defence Innovation (idex.gov.in)\n"
        "• <b>Bihar SCC</b>: 7nishchay-yuvaupmission.bihar.gov.in\n"
        "• <b>Smart India Hackathon</b>: sih.gov.in"
    )
    send_telegram(msg)


def main():
    print("=" * 60)
    print(f"  OPPORTUNITY BOT RUN: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    # Load previously seen (dict: hash -> timestamp)
    seen = load_seen()
    print(f"[INFO] Previously seen: {len(seen)} opportunities")

    # ---- Fetch from all sources ----
    all_opportunities = []

    # Government Jobs
    all_opportunities.extend(fetch_govt_jobs())
    all_opportunities.extend(fetch_jagranjosh())

    # Unstop (India's biggest platform - scholarships, internships, hackathons, competitions)
    all_opportunities.extend(fetch_unstop_scholarships())
    all_opportunities.extend(fetch_unstop_internships())
    all_opportunities.extend(fetch_unstop_hackathons())
    all_opportunities.extend(fetch_unstop_competitions())

    # ScholarshipsInIndia (extra scholarship coverage)
    all_opportunities.extend(fetch_scholarshipsinindia())

    # Global opportunity aggregators (fellowships, scholarships, internships worldwide)
    all_opportunities.extend(fetch_generic_rss(
        "https://opportunitiesforyouth.org/feed/", "OpportunitiesForYouth"))
    all_opportunities.extend(fetch_generic_rss(
        "https://opportunitiescircle.com/feed/", "OpportunitiesCircle"))
    all_opportunities.extend(fetch_generic_rss(
        "https://opportunitydesk.org/feed/", "OpportunityDesk"))
    all_opportunities.extend(fetch_generic_rss(
        "https://scholarshiproar.com/feed/", "ScholarshipRoar"))
    all_opportunities.extend(fetch_generic_rss(
        "https://opportunitycell.com/feed/", "OpportunityCell"))
    all_opportunities.extend(fetch_generic_rss(
        "https://oyaop.com/feed/", "Oyaop"))

    # HackerEarth (hackathons + hiring challenges)
    all_opportunities.extend(fetch_hackerearth())

    # Devpost (International hackathons)
    all_opportunities.extend(fetch_devpost_hackathons())

    # Codeforces (upcoming coding contests)
    all_opportunities.extend(fetch_codeforces())

    # Community GitHub repos (structured JSON internship listings)
    all_opportunities.extend(fetch_github_internships())
    all_opportunities.extend(fetch_speedyapply_intl())
    all_opportunities.extend(fetch_github_newgrad())

    # foundit.in (job board API - tech internships)
    all_opportunities.extend(fetch_foundit())

    # Internshala (India's biggest internship platform)
    all_opportunities.extend(fetch_internshala())

    # GovAI (governance.ai) - high-value AI governance fellowships
    all_opportunities.extend(fetch_governance_ai())

    # Sarkari Result (latest govt jobs)
    all_opportunities.extend(fetch_sarkari_result())

    # Devfolio (Web3 / Community hackathons)
    all_opportunities.extend(fetch_devfolio())

    # AICTE Internships
    all_opportunities.extend(fetch_aicte_internships())

    # ISTI Portal (Fellowships)
    all_opportunities.extend(fetch_isti_portal())

    # MyGov (Govt Campaigns/Competitions)
    all_opportunities.extend(fetch_mygov())

    # Trigger static reminders on the 1st of the month
    send_monthly_reminders()

    total_fetched = len(all_opportunities)
    print(f"\n{'='*60}")
    print(f"[INFO] TOTAL FETCHED: {total_fetched}")
    print(f"{'='*60}")

    # ---- Filter out junk (exam results, answer keys, admit cards, etc.) ----
    before = len(all_opportunities)
    all_opportunities = [o for o in all_opportunities if not is_junk(o["title"])]
    print(f"[INFO] Removed {before - len(all_opportunities)} junk listings "
          f"(results/answer-keys/admit-cards). Kept {len(all_opportunities)}")

    # ---- Hard blocklist (marketing/sales/HR/content/video etc. - never wanted) ----
    before = len(all_opportunities)
    all_opportunities = [o for o in all_opportunities if not is_blocked(o["title"])]
    print(f"[INFO] Removed {before - len(all_opportunities)} blocklisted listings "
          f"(marketing/sales/HR/content/etc.). Kept {len(all_opportunities)}")

    # ---- Cross-source dedup (same role appearing on multiple sources) ----
    before = len(all_opportunities)
    deduped = []
    seen_keys = set()
    for opp in all_opportunities:
        key = normalize_key(opp["title"])
        if key and key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(opp)
    all_opportunities = deduped
    print(f"[INFO] Removed {before - len(all_opportunities)} cross-source duplicates. "
          f"Kept {len(all_opportunities)}")

    # ---- Deduplicate against seen (now a dict: hash -> timestamp) ----
    now_ts = int(time.time())
    new_opportunities = []
    for opp in all_opportunities:
        h = make_hash(opp["title"] + opp["link"])
        if h not in seen:
            new_opportunities.append(opp)
            seen[h] = now_ts

    print(f"[INFO] New (unseen): {len(new_opportunities)}")

    if not new_opportunities:
        print("[INFO] No new opportunities found. Exiting.")
        save_seen(seen)
        return

    # ---- Classify with LLM ----
    relevant = classify_with_llm(new_opportunities)
    print(f"[INFO] Relevant after LLM filter: {len(relevant)}")

    if not relevant:
        print("[INFO] No relevant opportunities after filtering. Exiting.")
        save_seen(seen)
        return

    # ---- Send to Telegram (grouped category digest) ----
    send_digest(relevant, len(new_opportunities), total_fetched)

    # ---- Save updated seen list ----
    save_seen(seen)
    print(f"\n[DONE] Sent digest with {len(relevant)} opportunities to Telegram.")
    print(f"[DONE] Total tracked: {len(seen)} opportunities")


if __name__ == "__main__":
    main()

