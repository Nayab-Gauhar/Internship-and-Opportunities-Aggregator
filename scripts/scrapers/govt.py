import re
import xml.etree.ElementTree as ET
from core.utils import fetch_url

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
