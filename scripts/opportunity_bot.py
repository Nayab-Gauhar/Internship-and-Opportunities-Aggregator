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

SEEN_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "seen.json")

# Your profile - LLM uses this to judge relevance
USER_PROFILE = """
- 3rd year B.Tech CSE student (entering 5th semester)
- College: JIS College of Engineering, West Bengal
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
    """Use Groq LLM to filter relevant opportunities based on user profile.

    Falls back to keyword_relevance() when the LLM is unavailable or errors out,
    so the bot never floods Telegram with everything.
    """
    if not opportunities:
        return []

    if not GROQ_API_KEY:
        print("[WARN] No GROQ_API_KEY set. Using keyword fallback filter.")
        return [o for o in opportunities if keyword_relevance(o)]

    print(f"[INFO] Classifying {len(opportunities)} opportunities with Groq LLM...")

    relevant = []
    batch_size = 15

    for i in range(0, len(opportunities), batch_size):
        batch = opportunities[i:i + batch_size]

        listings_text = ""
        for idx, opp in enumerate(batch):
            listings_text += f"\n{idx+1}. [{opp['category']}] {opp['title']}"
            if opp['description']:
                listings_text += f" - {opp['description'][:100]}"

        prompt = f"""You are a career opportunity classifier for an Indian engineering student.
Given the student profile and a list of opportunities, return ONLY the numbers of 
opportunities that are RELEVANT to this specific student.

STUDENT PROFILE:
{USER_PROFILE}

OPPORTUNITIES:
{listings_text}

CLASSIFICATION RULES:
- RELEVANT: CS/IT/AI/ML/tech related jobs, internships in software/AI/data science,
  engineering scholarships, tech hackathons, coding competitions, research fellowships
- RELEVANT: Government IT/tech positions (even if seem senior - good to track early)
- RELEVANT: General engineering scholarships the student is eligible for
- NOT RELEVANT: MBA/law/medical/agriculture/arts-only roles
- NOT RELEVANT: Sales, marketing, HR, content writing, video editing internships
- NOT RELEVANT: Scholarships restricted to categories student doesn't belong to
- NOT RELEVANT: Positions requiring qualifications student doesn't have (PG, PhD)

Return ONLY relevant opportunity numbers as a comma-separated list (e.g., "1,3,5,7")
If NONE are relevant, return exactly "NONE"

RELEVANT NUMBERS:"""

        url = "https://api.groq.com/openai/v1/chat/completions"
        payload = json.dumps({
            "model": GROQ_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "max_tokens": 100
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
                print(f"[INFO] LLM batch {i//batch_size + 1} response: {answer}")

                if answer.upper() == "NONE":
                    continue

                # Parse numbers from response
                numbers = re.findall(r'\d+', answer)
                for num_str in numbers:
                    idx = int(num_str) - 1
                    if 0 <= idx < len(batch):
                        relevant.append(batch[idx])

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
            relevant.extend([o for o in batch if keyword_relevance(o)])
        except Exception as e:
            print(f"[ERROR] Groq API error: {e}")
            relevant.extend([o for o in batch if keyword_relevance(o)])

        time.sleep(2)  # Rate limiting between batches (free tier is strict)

    print(f"[INFO] LLM classified {len(relevant)} as relevant out of {len(opportunities)} total")
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

MAX_PER_CATEGORY = 8   # cap items shown per category to keep messages readable


def esc(text):
    """Escape HTML special chars for Telegram HTML parse mode."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_category_message(category, items):
    """Build one clean Telegram message for all items in a category."""
    emoji, label = CATEGORY_META.get(category, ("\U0001f4cc", category.title()))
    shown = items[:MAX_PER_CATEGORY]

    msg = f"{emoji} <b>{label.upper()}</b>  ({len(items)} new)\n"
    msg += "\u2501" * 18 + "\n"

    for i, opp in enumerate(shown, 1):
        title = esc(opp["title"][:90])
        msg += f"\n<b>{i}. {title}</b>\n"
        if opp.get("date"):
            msg += f"   \U0001f4c5 <i>{esc(str(opp['date'])[:40])}</i>\n"
        msg += f"   \U0001f517 <a href=\"{opp['link']}\">Apply / Details</a>"
        msg += f"  \u00b7  <i>{esc(opp['source'])}</i>\n"

    if len(items) > MAX_PER_CATEGORY:
        msg += f"\n\u2795 <i>+{len(items) - MAX_PER_CATEGORY} more {label.lower()}</i>"

    return msg


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

    # ---- One message per category ----
    for cat in CATEGORY_ORDER:
        if cat in grouped:
            send_telegram(build_category_message(cat, grouped[cat]))


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

    print(f"\n{'='*60}")
    print(f"[INFO] TOTAL FETCHED: {len(all_opportunities)}")
    print(f"{'='*60}")

    # ---- Filter out junk (exam results, answer keys, admit cards, etc.) ----
    before = len(all_opportunities)
    all_opportunities = [o for o in all_opportunities if not is_junk(o["title"])]
    print(f"[INFO] Removed {before - len(all_opportunities)} junk listings "
          f"(results/answer-keys/admit-cards). Kept {len(all_opportunities)}")

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
