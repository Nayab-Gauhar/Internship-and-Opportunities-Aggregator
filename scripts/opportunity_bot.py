"""
Unified Opportunity Bot
-----------------------
Fetches opportunities from multiple sources, classifies relevance using
Groq LLM, and sends matches to Telegram.

WORKING SOURCES:
1. FreeJobAlert RSS - Government jobs (verified working)
2. Unstop API - Scholarships, Internships, Hackathons, Competitions (verified working)
3. Devpost API - International Hackathons (verified working)

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
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant").strip()

# Minimum LLM relevance score (0-10) for an opportunity to be sent. Higher = stricter.
MIN_RELEVANCE_SCORE = int(os.environ.get("MIN_RELEVANCE_SCORE", "6"))

SEEN_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "seen.json")

# Your profile - LLM uses this to judge relevance
USER_PROFILE = """
- 3rd year B.Tech CSE student (entering 5th semester)
- College: Haldia Institute of Technology, West Bengal
- Home state: Bihar
- CGPA: 8.7/10
- Interests: AI/ML, Computer Vision, Edge AI (Jetson), Agentic AI, LLMs, Deep Learning
- Skills: Python, C++, FastAPI, Docker, YOLO, PyTorch, LangChain
- Currently doing: FAST-SF fellowship at NIT Puducherry (edge AI surveillance)
- Looking for: Government jobs (technical/IT cadre), internships (AI/ML/software),
  scholarships, fellowships, hackathons, research opportunities, competitions
- NOT interested in: MBA, law, medical, agriculture, arts/humanities-only roles,
  sales/marketing/HR internships, content writing roles
"""

# ============================================================
# UTILITIES
# ============================================================

def load_seen():
    """Load previously seen opportunity hashes."""
    try:
        with open(SEEN_FILE, "r") as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_seen(seen_set):
    """Save seen hashes to file."""
    os.makedirs(os.path.dirname(SEEN_FILE), exist_ok=True)
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen_set), f)


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
]
# Short/risky tokens: matched only as whole words (avoids e.g. "Sales" hitting
# "Salesforce", or "ops" hitting "DevOps").
BLOCKLIST_WORDS = ["hr", "bd", "mis", "pr", "ba", "sales", "seo", "accounts", "ops"]


def is_blocked(title):
    """Return True if the role is in the user's NOT-interested blocklist."""
    t = " " + re.sub(r'[^a-z0-9 ]', ' ', title.lower()) + " "
    t = re.sub(r'\s+', ' ', t)
    if any(kw in t for kw in BLOCKLIST_KEYWORDS):
        return True
    if any(f" {w} " in t for w in BLOCKLIST_WORDS):
        return True
    return False


def fetch_url(url, headers=None):
    """Fetch URL content with basic error handling."""
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"[ERROR] Failed to fetch {url}: {e}")
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
        "Referer": "https://www.foundit.in/search/jobs",
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


def classify_with_llm(opportunities):
    """Score each opportunity 0-10 for relevance to the user, keep those scoring
    >= MIN_RELEVANCE_SCORE, and return them sorted best-first (with `_score` set).

    Falls back to keyword_relevance() when the LLM is unavailable or errors out.
    """
    if not opportunities:
        return []

    if not GROQ_API_KEY:
        print("[WARN] No GROQ_API_KEY set. Using keyword fallback filter.")
        kept = [o for o in opportunities if keyword_relevance(o)]
        for o in kept:
            o["_score"] = 0
        return kept

    print(f"[INFO] Scoring {len(opportunities)} opportunities with Groq LLM "
          f"(threshold {MIN_RELEVANCE_SCORE}/10)...")

    relevant = []
    batch_size = 15

    for i in range(0, len(opportunities), batch_size):
        batch = opportunities[i:i + batch_size]

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

    # Sort best-first by score
    relevant.sort(key=lambda o: o.get("_score", 0), reverse=True)
    print(f"[INFO] LLM kept {len(relevant)} of {len(opportunities)} "
          f"(score >= {MIN_RELEVANCE_SCORE})")
    return relevant


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


def send_digest(relevant, total_new):
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


def main():
    print("=" * 60)
    print(f"  OPPORTUNITY BOT RUN: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    # Load previously seen
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

    # Community GitHub repos (structured JSON internship listings)
    all_opportunities.extend(fetch_github_internships())
    all_opportunities.extend(fetch_speedyapply_intl())

    # foundit.in (job board API - tech internships)
    all_opportunities.extend(fetch_foundit())

    # GovAI (governance.ai) - high-value AI governance fellowships
    all_opportunities.extend(fetch_governance_ai())

    print(f"\n{'='*60}")
    print(f"[INFO] TOTAL FETCHED: {len(all_opportunities)}")
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

    # ---- Deduplicate against seen ----
    new_opportunities = []
    for opp in all_opportunities:
        h = make_hash(opp["title"] + opp["link"])
        if h not in seen:
            new_opportunities.append(opp)
            seen.add(h)

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
    send_digest(relevant, len(new_opportunities))

    # ---- Save updated seen list ----
    save_seen(seen)
    print(f"\n[DONE] Sent digest with {len(relevant)} opportunities to Telegram.")
    print(f"[DONE] Total tracked: {len(seen)} opportunities")


if __name__ == "__main__":
    main()
