import re, json, time
import xml.etree.ElementTree as ET
from datetime import datetime
from core.utils import fetch_url

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
