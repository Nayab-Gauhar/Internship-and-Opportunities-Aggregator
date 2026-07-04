import urllib.request, re, ssl
ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE

def fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        return urllib.request.urlopen(req, context=ctx, timeout=10).read().decode()
    except Exception as e:
        return f"Error: {e}"

print("--- NITI Aayog ---")
print(fetch("https://workforindia.niti.gov.in/intern/InternshipEntry/PCInternshipEntry.aspx")[:200])

print("--- IndiaAI ---")
print(fetch("https://fellowship.indiaai.gov.in")[:200])

print("--- iDEX ---")
print(fetch("https://idex.gov.in")[:200])

print("--- PM Internship ---")
print(fetch("https://pminternship.mca.gov.in/")[:200])
