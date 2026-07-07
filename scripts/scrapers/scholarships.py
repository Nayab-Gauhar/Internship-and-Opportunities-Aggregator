import re
import json
import xml.etree.ElementTree as ET
from core.utils import fetch_url

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
