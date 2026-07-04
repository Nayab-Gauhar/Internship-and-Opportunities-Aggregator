"""
Unified Opportunity Bot (Orchestrator)
--------------------------------------
Fetches opportunities from 25+ sources, classifies relevance using
Groq LLM, and sends matches to Telegram.
"""

import os
import json
import time
import urllib.request
import urllib.parse
from datetime import datetime

from core.utils import load_seen, save_seen, make_hash, normalize_key, is_junk, is_blocked
from core.telegram import send_digest, send_monthly_reminders
from scrapers import govt, hackathons, internships, scholarships, fellowships

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()

# Groq model - update here if the model gets deprecated (see https://console.groq.com/docs/models)
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.1-70b-versatile").strip()

# Minimum LLM relevance score (0-10) for an opportunity to be sent. Higher = stricter.
MIN_RELEVANCE_SCORE = int(os.environ.get("MIN_RELEVANCE_SCORE", "6"))

SEEN_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "seen.json")

# Your profile - LLM uses this to judge relevance
USER_PROFILE = """
- 3rd year B.Tech CSE student (entering 5th semester)
- College: LNCT Bhopal, Madhya Pradesh
- Interests: AI/ML, Software Development, Data Structures & Algorithms, Web Development
- Skills: Python, C++, Java, SQL, HTML, CSS, JavaScript
- Looking for: Software Engineering internships, AI/ML internships, hackathons,
  scholarships, fellowships, research opportunities, coding competitions
- NOT interested in: MBA, law, medical, agriculture, arts/humanities-only roles,
  sales/marketing/HR internships, content writing roles
"""

def classify_with_llm(opportunities):
    """Score each opportunity 0-10 for relevance to the user, keep those scoring
    >= MIN_RELEVANCE_SCORE, and return them sorted best-first (with `_score` set).

    Categories in AUTO_APPROVE_CATEGORIES skip the LLM entirely (auto-score 7).
    Falls back to keyword_relevance() when the LLM is unavailable or errors out.
    """
    if not opportunities:
        return []

    # --- Auto-approve obvious categories without burning LLM calls ---
    auto_kept = []
    needs_llm = []
    for opp in opportunities:
        if opp["category"] in AUTO_APPROVE_CATEGORIES:
            opp["_score"] = 7
            auto_kept.append(opp)
        else:
            needs_llm.append(opp)

    if auto_kept:
        print(f"[INFO] Auto-approved {len(auto_kept)} hackathons/competitions/"
              f"scholarships/fellowships (skipped LLM)")

    if not GROQ_API_KEY:
        print("[WARN] No GROQ_API_KEY set. Using keyword fallback filter.")
        kept = [o for o in needs_llm if keyword_relevance(o)]
        for o in kept:
            o["_score"] = 0
        return auto_kept + kept

    print(f"[INFO] Scoring {len(needs_llm)} opportunities with Groq LLM "
          f"(threshold {MIN_RELEVANCE_SCORE}/10)...")


    relevant = []
    batch_size = 15

    for i in range(0, len(needs_llm), batch_size):
        batch = needs_llm[i:i + batch_size]

        listings_text = ""
        for idx, opp in enumerate(batch):
            listings_text += f"\n{idx+1}. [{opp['category']}] {opp['title']}"
            if opp['description']:
                listings_text += f" - {opp['description'][:100]}"

        prompt = f"""You are a career opportunity matcher for an Indian engineering student.
Score EACH opportunity from 0 to 10 for how relevant it is to THIS student.

STUDENT PROFILE:
{USER_PROFILE}

OPPORTUNITIES:
{listings_text}

SCORING GUIDE:
- 9-10: Perfect fit (AI/ML, software, data science, CS research, tech fellowship/scholarship matching their skills)
- 6-8: Good fit (general software/engineering/tech role, coding hackathon, eligible engineering scholarship)
- 3-5: Weak/uncertain fit (tangentially technical, or eligibility unclear)
- 0-2: Not relevant (sales, marketing, HR, content/video, non-tech, MBA/medical/law, needs PhD/PG, ineligible)

Respond with ONLY a JSON object mapping each opportunity number to its score:
{{"scores": {{"1": 9, "2": 2, "3": 7}}}}
Include every number from the list."""

        url = "https://api.groq.com/openai/v1/chat/completions"
        payload = json.dumps({
            "model": GROQ_MODEL,
            "messages": [
                {"role": "system", "content": "You are a precise scorer. You respond ONLY with valid JSON, no explanations."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.0,
            "max_tokens": 300,
            "response_format": {"type": "json_object"}
        })

        req = urllib.request.Request(url, data=payload.encode())
        req.add_header("Authorization", f"Bearer {GROQ_API_KEY}")
        req.add_header("Content-Type", "application/json")
        # Groq's API is behind Cloudflare, which blocks the default Python urllib
        # User-Agent (causes "403 error code: 1010"). A browser UA avoids the block.
        req.add_header("User-Agent", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode())
                answer = result["choices"][0]["message"]["content"].strip()
                print(f"[INFO] LLM batch {i//batch_size + 1}: {answer[:140]}")

                scores = {}
                try:
                    scores = json.loads(answer).get("scores", {})
                except (json.JSONDecodeError, AttributeError):
                    # Fallback: pull "num": score pairs out of stray text
                    for n, s in re.findall(r'"(\d+)"\s*:\s*(\d+)', answer):
                        scores[n] = int(s)

                for idx, opp in enumerate(batch):
                    try:
                        sc = int(scores.get(str(idx + 1), 0))
                    except (ValueError, TypeError):
                        sc = 0
                    if sc >= MIN_RELEVANCE_SCORE:
                        opp["_score"] = sc
                        relevant.append(opp)

        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode()[:300]
            except Exception:
                pass
            print(f"[ERROR] Groq HTTP {e.code}: {body}")
            if e.code in (401, 403):
                print("[HINT] Check your GROQ_API_KEY secret is valid & active. "
                      f"If the model '{GROQ_MODEL}' is deprecated, set a GROQ_MODEL secret "
                      "to a current model from https://console.groq.com/docs/models")
            # Fall back to keyword filter for this batch (don't flood)
            for o in batch:
                if keyword_relevance(o):
                    o["_score"] = 0
                    relevant.append(o)
        except Exception as e:
            print(f"[ERROR] Groq API error: {e}")
            for o in batch:
                if keyword_relevance(o):
                    o["_score"] = 0
                    relevant.append(o)

        time.sleep(2)  # Rate limiting between batches (free tier is strict)

    # Merge auto-approved + LLM-approved, sort best-first
    all_relevant = auto_kept + relevant
    all_relevant.sort(key=lambda o: o.get("_score", 0), reverse=True)
    print(f"[INFO] LLM kept {len(relevant)} of {len(needs_llm)} "
          f"(score >= {MIN_RELEVANCE_SCORE}), "
          f"+ {len(auto_kept)} auto-approved = {len(all_relevant)} total")
    return all_relevant

def main():
    print("=" * 60)
    print(f"  OPPORTUNITY BOT RUN: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    # Load previously seen (dict: hash -> timestamp)
    seen = load_seen()
    print(f"[INFO] Previously seen: {len(seen)} opportunities")

    # ---- Fetch from all sources ----
    all_opportunities = []

    # Government Jobs
    all_opportunities.extend(govt.fetch_govt_jobs())
    all_opportunities.extend(govt.fetch_jagranjosh())

    # Unstop (India's biggest platform - scholarships, internships, hackathons, competitions)
    all_opportunities.extend(scholarships.fetch_unstop_scholarships())
    all_opportunities.extend(scholarships.fetch_unstop_internships())
    all_opportunities.extend(scholarships.fetch_unstop_hackathons())
    all_opportunities.extend(scholarships.fetch_unstop_competitions())

    # ScholarshipsInIndia (extra scholarship coverage)
    all_opportunities.extend(scholarships.fetch_scholarshipsinindia())

    # Global opportunity aggregators (fellowships, scholarships, internships worldwide)
    all_opportunities.extend(scholarships.fetch_generic_rss(
        "https://opportunitiesforyouth.org/feed/", "OpportunitiesForYouth"))
    all_opportunities.extend(scholarships.fetch_generic_rss(
        "https://opportunitiescircle.com/feed/", "OpportunitiesCircle"))
    all_opportunities.extend(scholarships.fetch_generic_rss(
        "https://opportunitydesk.org/feed/", "OpportunityDesk"))
    all_opportunities.extend(scholarships.fetch_generic_rss(
        "https://scholarshiproar.com/feed/", "ScholarshipRoar"))
    all_opportunities.extend(scholarships.fetch_generic_rss(
        "https://opportunitycell.com/feed/", "OpportunityCell"))
    all_opportunities.extend(scholarships.fetch_generic_rss(
        "https://oyaop.com/feed/", "Oyaop"))

    # HackerEarth (hackathons + hiring challenges)
    all_opportunities.extend(hackathons.fetch_hackerearth())

    # Devpost (International hackathons)
    all_opportunities.extend(hackathons.fetch_devpost_hackathons())

    # Codeforces (upcoming coding contests)
    all_opportunities.extend(hackathons.fetch_codeforces())

    # Community GitHub repos (structured JSON internship listings)
    all_opportunities.extend(internships.fetch_github_internships())
    all_opportunities.extend(internships.fetch_speedyapply_intl())
    all_opportunities.extend(internships.fetch_github_newgrad())

    # foundit.in (job board API - tech internships)
    all_opportunities.extend(internships.fetch_foundit())

    # Internshala (India's biggest internship platform)
    all_opportunities.extend(internships.fetch_internshala())

    # GovAI (governance.ai) - high-value AI governance fellowships
    all_opportunities.extend(fellowships.fetch_governance_ai())

    # Sarkari Result (latest govt jobs)
    all_opportunities.extend(govt.fetch_sarkari_result())

    # Devfolio (Web3 / Community hackathons)
    all_opportunities.extend(hackathons.fetch_devfolio())

    # AICTE Internships
    all_opportunities.extend(internships.fetch_aicte_internships())

    # ISTI Portal (Fellowships)
    all_opportunities.extend(fellowships.fetch_isti_portal())

    # MyGov (Govt Campaigns/Competitions)
    all_opportunities.extend(govt.fetch_mygov())

    # Trigger static reminders on the 1st of the month
    send_monthly_reminders()

    total_fetched = len(all_opportunities)
    print(f"\n{'='*60}")
    print(f"[INFO] TOTAL FETCHED: {total_fetched}")
    print(f"{'='*60}")

    # ---- Filter out junk (exam results, answer keys, admit cards, etc.) ----
    before = len(all_opportunities)
    all_opportunities = [o for o in all_opportunities if not is_junk(o["title"])]
    print(f"[INFO] Removed {before - len(all_opportunities)} junk listings "
          f"(results/answer-keys/admit-cards). Kept {len(all_opportunities)}")

    # ---- Hard blocklist (marketing/sales/HR/content/video etc. - never wanted) ----
    before = len(all_opportunities)
    all_opportunities = [o for o in all_opportunities if not is_blocked(o["title"])]
    print(f"[INFO] Removed {before - len(all_opportunities)} blocklisted listings "
          f"(marketing/sales/HR/content/etc.). Kept {len(all_opportunities)}")

    # ---- Cross-source dedup (same role appearing on multiple sources) ----
    before = len(all_opportunities)
    deduped = []
    seen_keys = set()
    for opp in all_opportunities:
        key = normalize_key(opp["title"])
        if key and key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(opp)
    all_opportunities = deduped
    print(f"[INFO] Removed {before - len(all_opportunities)} cross-source duplicates. "
          f"Kept {len(all_opportunities)}")

    # ---- Deduplicate against seen (now a dict: hash -> timestamp) ----
    now_ts = int(time.time())
    new_opportunities = []
    for opp in all_opportunities:
        h = make_hash(opp["title"] + opp["link"])
        if h not in seen:
            new_opportunities.append(opp)
            seen[h] = now_ts

    print(f"[INFO] New (unseen): {len(new_opportunities)}")

    if not new_opportunities:
        print("[INFO] No new opportunities found. Exiting.")
        save_seen(seen)
        return

    # ---- Classify with LLM ----
    relevant = classify_with_llm(new_opportunities)
    print(f"[INFO] Relevant after LLM filter: {len(relevant)}")

    if not relevant:
        print("[INFO] No relevant opportunities after filtering. Exiting.")
        save_seen(seen)
        return

    # ---- Send to Telegram (grouped category digest) ----
    send_digest(relevant, len(new_opportunities), total_fetched)

    # ---- Save updated seen list ----
    save_seen(seen)
    print(f"\n[DONE] Sent digest with {len(relevant)} opportunities to Telegram.")
    print(f"[DONE] Total tracked: {len(seen)} opportunities")


if __name__ == "__main__":
    main()

if __name__ == "__main__":
    main()
