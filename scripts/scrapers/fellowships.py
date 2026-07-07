import re
from core.utils import fetch_url

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
