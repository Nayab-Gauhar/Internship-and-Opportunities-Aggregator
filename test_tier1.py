import urllib.request, re, json
import ssl
ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE

def fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=10) as r:
            return r.read().decode()
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return ""

print("--- Sarkari Result ---")
html = fetch("https://www.sarkariresult.com/")
# Find the box for Latest Jobs - it's usually inside <div id="post">
# Let's just find all links in the main content area that look like jobs
links = re.findall(r'<a href="(https://www\.sarkariresult\.com/\d+/[^"]+)"[^>]*>(.*?)</a>', html)
if not links:
    links = re.findall(r'<a href="(https://www\.sarkariresult\.com/[a-z]+/[^"]+)"[^>]*>(.*?)</a>', html)
print(f"Sarkari Result links: {len(links)}")
for l, t in links[:3]: print(f"  {re.sub('<[^>]+>', '', t).strip()} -> {l}")

print("\n--- AICTE ---")
html = fetch("https://internship.aicte-india.org/")
# AICTE might be a dynamic page, let's look for internships
b = html[:500]
print("AICTE start:", b.replace('\n', ' '))
links = re.findall(r'href="([^"]+)"', html)
int_links = [l for l in links if 'internship' in l.lower() and l.startswith('http')]
print(f"AICTE internship links: {len(int_links)}")

print("\n--- ISTI ---")
html = fetch("https://www.indiascienceandtechnology.gov.in/listingpage/internships")
titles = re.findall(r'<div class="title-field[^"]*">\s*<a href="([^"]+)">(.*?)</a>', html, re.S)
print(f"ISTI links: {len(titles)}")
for l, t in titles[:3]: print(f"  {t.strip()} -> https://www.indiascienceandtechnology.gov.in{l}")

print("\n--- MyGov ---")
html = fetch("https://www.mygov.in/")
camps = re.findall(r'<a href="([^"]+)"[^>]*>.*?<div class="campaign_title"[^>]*>(.*?)</div>', html, re.S)
print(f"MyGov campaigns: {len(camps)}")
for l, t in camps[:3]: print(f"  {t.strip()} -> {l}")
