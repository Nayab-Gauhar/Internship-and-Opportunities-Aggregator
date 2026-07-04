import urllib.request, urllib.error
import ssl

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

def test_url(name, url):
    req = urllib.request.Request(url)
    req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
            print(f'✅ {name}: OK ({resp.status})')
    except urllib.error.HTTPError as e:
        print(f'❌ {name}: HTTP {e.code}')
    except Exception as e:
        print(f'❌ {name}: {e}')

urls = {
    'Sarkari Result': 'https://www.sarkariresult.com/',
    'AICTE Internship': 'https://internship.aicte-india.org/',
    'PM Internship': 'https://pminternship.mca.gov.in/',
    'MyGov': 'https://www.mygov.in/',
    'NITI Aayog': 'https://workforindia.niti.gov.in/intern/InternshipEntry/PCInternshipEntry.aspx',
    'ISTI': 'https://www.indiascienceandtechnology.gov.in/listingpage/internships',
    'Devfolio': 'https://api.devfolio.co/api/search/hackathons?q=&page=1&limit=20',
    'myScheme': 'https://www.myscheme.gov.in/',
    'NSP': 'https://scholarships.gov.in/',
    'IndiaAI': 'https://indiaai.gov.in/',
    'DRDO Careers': 'https://www.drdo.gov.in/drdo/careers',
    'ISRO Internships': 'https://www.isro.gov.in/InternshipAndProjects.html',
    'SIH': 'https://sih.gov.in/',
    'iDEX': 'https://idex.gov.in/',
    'Bihar Student CC': 'https://www.7nishchay-yuvaupmission.bihar.gov.in/'
}

for name, url in urls.items():
    test_url(name, url)
