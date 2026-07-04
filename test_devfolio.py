import urllib.request, re, json

def fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    return urllib.request.urlopen(req, timeout=10).read().decode()

html = fetch("https://devfolio.co/hackathons")
b_match = re.search(r'"buildId":"([^"]+)"', html)
if b_match:
    build_id = b_match.group(1)
    print(f"Build ID: {build_id}")
    url = f"https://devfolio.co/_next/data/{build_id}/hackathons.json"
    print("Fetching", url)
    try:
        data = json.loads(fetch(url))
        queries = data.get("pageProps", {}).get("dehydratedState", {}).get("queries", [])
        if queries:
            print("Found queries!")
            for q in queries:
                if 'data' in q.get('state', {}):
                    hits = q['state']['data'].get('hits', [])
                    for h in hits[:3]:
                        print(f"  - {h.get('name')} (https://{h.get('slug')}.devfolio.co)")
    except Exception as e:
        print("Failed to fetch/parse", e)
