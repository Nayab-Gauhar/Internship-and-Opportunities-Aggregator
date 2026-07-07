# Opportunity Bot

Automated Telegram bot that finds government jobs, scholarships, fellowships, hackathons, internships, and competitions relevant to your profile — powered by LLM classification.

## How it works

```
SOURCES (25):
├── GOVT JOBS
│   ├── SarkariResult         → State/Central Govt jobs
│   ├── FreeJobAlert RSS      → UPSC, SSC, Railway, PSU, State
│   └── JagranJosh            → Recruitment notifications
├── UNSTOP API                → Scholarships, Internships, Hackathons, Competitions
├── SCHOLARSHIPS
│   ├── ScholarshipsInIndia   → India scholarships
│   └── ScholarshipRoar       → Global fully-funded scholarships
├── INTERNSHIPS
│   ├── AICTE Internship      → Govt internship portal
│   ├── Internshala           → India's largest internship platform
│   ├── foundit.in API        → Tech internships (Monster India)
│   └── GitHub/speedyapply    → International SWE internships
├── GLOBAL AGGREGATORS
│   ├── OpportunitiesForYouth → Internships, fellowships, jobs
│   ├── OpportunitiesCircle   → AI fellowships, tech scholarships
│   ├── OpportunityDesk       → Fellowships, grants
│   ├── OpportunityCell       → Engineering internships, stipend roles
│   └── Oyaop                 → Scholarships, fellowships, competitions
├── HACKATHONS & COMPETITIONS
│   ├── MyGov                 → Govt campaigns and competitions
│   ├── Devfolio              → Web3/Community hackathons
│   ├── HackerEarth API       → Hackathons + hiring challenges
│   ├── Devpost API           → International hackathons
│   └── Codeforces API        → Upcoming coding contests
├── FELLOWSHIPS
│   ├── ISTI Portal           → Govt fellowships & research funding
│   └── GovAI                 → AI governance/policy fellowships
└── GITHUB REPOS (community-maintained, structured JSON)
    ├── SimplifyJobs/Summer2026-Internships → tech internships
    └── SimplifyJobs/New-Grad-Positions     → new-grad SWE roles
         ↓
    Fetch new listings (~300/run)
         ↓
    Deduplicate (skip already notified, 30-day auto-prune)
         ↓
    LLM Classification (Groq free tier - llama-3.1-70b)
    "Is this relevant for a 3rd year CSE student interested in AI/ML?"
         ↓
    Send relevant ones → Telegram
```

## Setup (10 minutes, one time)

### Step 1: Create Telegram Bot
1. Open Telegram, search `@BotFather`, message it
2. Send `/newbot`, give it a name
3. Copy the **token** it gives you
4. Send any message to your new bot
5. Open in browser: `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
6. Find `"chat":{"id":123456789}` — that number is your **chat ID**

### Step 2: Get Groq API Key (free)
1. Go to https://console.groq.com
2. Sign up / login
3. Go to API Keys → Create API Key
4. Copy the key

### Step 3: Create GitHub Repository
1. Create a new repo on GitHub (can be private, free Actions minutes are enough)
2. Upload these files maintaining the folder structure:
   ```
   .github/workflows/opportunity-bot.yml
   scripts/opportunity_bot.py
   data/seen.json
   ```

### Step 4: Add Secrets
Go to repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**:

| Secret Name | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Your bot token from Step 1 |
| `TELEGRAM_CHAT_ID` | Your chat ID from Step 1 |
| `GROQ_API_KEY` | Your Groq key from Step 2 |

### Step 5: Enable Write Permission
Repo → **Settings** → **Actions** → **General** → scroll to **Workflow permissions** → select **"Read and write permissions"** → Save

### Step 6: Test It!
1. Go to **Actions** tab in your repo
2. Click **"Opportunity Bot"** workflow
3. Click **"Run workflow"** button
4. Check your Telegram — notifications should arrive!

## Schedule
Runs automatically twice daily:
- **8:00 AM IST** (morning)
- **6:00 PM IST** (evening)

You can also run manually anytime from Actions tab.

## What Gets Notified

The bot sends you opportunities tagged by category:
- 🏛️ `[GOV JOB]` — Government technical/IT positions
- 🎓 `[SCHOLARSHIP]` — Engineering & tech scholarships
- 💼 `[INTERNSHIP]` — AI/ML/Software internships
- 🚀 `[HACKATHON]` — Tech hackathons (India + International)
- 🏆 `[COMPETITION]` — Coding/hiring challenges

The LLM filters out irrelevant stuff (medical jobs, sales internships, MBA scholarships, etc.)

## Customization

### Change your profile
Edit `USER_PROFILE` in `scripts/opportunity_bot.py` — the LLM uses this to decide what's relevant.

### Add more sources
Add a new `fetch_*()` function in the script and call it in `main()`.

### Change notification frequency
Edit cron in `.github/workflows/opportunity-bot.yml`

## File Structure
```
opportunity-bot/
├── .github/workflows/
│   └── opportunity-bot.yml    # GitHub Actions workflow (schedule + trigger)
├── scripts/
│   └── opportunity_bot.py     # Main bot (scrapers + LLM + Telegram)
├── data/
│   └── seen.json              # Deduplication tracker (auto-updated)
└── README.md
```

## Troubleshooting

| Problem | Fix |
|---|---|
| "Write access not granted" | Settings → Actions → General → Workflow permissions → "Read and write" |
| "Push rejected" | Already fixed in workflow (git pull --rebase before push) |
| No Telegram messages | Check TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID secrets are correct |
| All opportunities sent (no filter) | Check GROQ_API_KEY secret is set correctly |
| Rate limit errors from Groq | Normal on free tier — reduce `per_page` in Unstop calls or increase sleep |
