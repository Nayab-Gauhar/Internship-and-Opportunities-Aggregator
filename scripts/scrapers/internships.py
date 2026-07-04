import re, json, time
import xml.etree.ElementTree as ET
from datetime import datetime
from core.utils import fetch_url

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
