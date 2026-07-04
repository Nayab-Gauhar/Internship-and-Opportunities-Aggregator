import urllib.request, re
import ssl
ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE

def fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    return urllib.request.urlopen(req, context=ctx, timeout=10).read().decode()

try:
    html_isti = fetch("https://www.indiascienceandtechnology.gov.in/listingpage/internships")
    # Just print the middle chunk to see what it looks like
    idx = html_isti.lower().find("internship")
    print(f"ISTI 'internship' index: {idx}")
    
    # Try finding typical node structures in Drupal/ISTI
    titles = re.findall(r'<span class="field-content"><a href="(/node/\d+)">([^<]+)</a></span>', html_isti)
    if titles:
        print(f"ISTI links found: {len(titles)}")
        for l,t in titles[:3]: print(t, l)
    else:
        # Fallback regex
        links = re.findall(r'<a href="(/node/\d+)">([^<]+)</a>', html_isti)
        print(f"ISTI fallback links: {len(links)}")
        for l,t in set(links): print(t, l)
        
except Exception as e:
    print(f"ISTI Error: {e}")

try:
    html_mygov = fetch("https://www.mygov.in/")
    # Find links on the page that have "campaign" or "task" or "innovation"
    links = re.findall(r'<a href="(https://(?:innovateindia|quiz|task)\.mygov\.in/[^"]+)"[^>]*>(.*?)</a>', html_mygov)
    print(f"\nMyGov links found: {len(links)}")
    for l,t in list(set(links))[:5]: print(f"  {re.sub('<[^>]+>', '', t).strip()} -> {l}")
except Exception as e:
    print(f"MyGov Error: {e}")
