import urllib.request, re, json
import ssl
ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE

def fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=10) as r:
            return r.read().decode()
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return ""

print("--- Sarkari Result ---")
html = fetch("https://www.sarkariresult.com/")
# Find the box for Latest Jobs
match = re.search(r'<div id="post".*?>(.*?)</div>', html, re.S)
if match:
    links = re.findall(r'<a href="(.*?)".*?>(.*?)</a>', match.group(1))
    print(f"Found {len(links)} links in first box")
    for l, text in links[:3]:
        print(f"  {text} -> {l}")

print("\n--- Devfolio ---")
html = fetch("https://devfolio.co/hackathons")
# Look for nextjs build id
b_match = re.search(r'"buildId":"(.*?)"', html)
if b_match:
    build_id = b_match.group(1)
    print(f"Devfolio build_id: {build_id}")
    json_url = f"https://devfolio.co/_next/data/{build_id}/hackathons.json"
    data_str = fetch(json_url)
    if data_str:
        try:
            data = json.loads(data_str)
            hackathons = data.get('pageProps', {}).get('dehydratedState', {}).get('queries', [])
            print(f"Found {len(hackathons)} Devfolio hackathon queries")
        except:
            print("Failed to parse devfolio json")
else:
    print("No Devfolio build ID found")

print("\n--- ISRO ---")
html = fetch("https://www.isro.gov.in/InternshipAndProjects.html")
links = re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.S)
valid_links = [ (l, re.sub(r'<[^>]+>', '', t).strip()) for l, t in links if 'internship' in l.lower() or 'project' in l.lower() ]
print(f"ISRO internship links: {len(valid_links)}")
for l, t in valid_links[:3]:
    if t: print(f"  {t} -> {l}")
