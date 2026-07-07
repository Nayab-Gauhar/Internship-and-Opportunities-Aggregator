"""
Unified Opportunity Bot (Orchestrator)
--------------------------------------
Fetches opportunities from 25+ sources, classifies relevance using
Groq LLM, and sends matches to Telegram.
"""

import json
import time
import re
import urllib.request
import urllib.error
from datetime import datetime

from core.config import (
    USER_PROFILE, GROQ_API_KEY, GROQ_MODEL, MIN_RELEVANCE_SCORE,
    LLM_BATCH_SIZE, AUTO_APPROVE_CATEGORIES,
)
from core.utils import (
    load_seen, save_seen, make_hash, normalize_key, is_junk, is_blocked,
    should_drop_geo, keyword_relevance, _source_errors,
)
from core.telegram import send_digest, send_monthly_reminders, send_flagship_reminders
from scrapers import govt, hackathons, internships, scholarships, fellowships


# ============================================================
# SOURCE REGISTRY
# ============================================================
# Each entry is (label, fetch_callable). To add a source, add one line here —
# no need to touch main(). Every source is fetched in isolation: if one crashes
# or times out, the others still run and the failure is reported in the digest.
def _build_sources():
    return [
        # ---- Government jobs ----
        ("FreeJobAlert", govt.fetch_govt_jobs),
        ("JagranJosh", govt.fetch_jagranjosh),
        # SarkariResult DISABLED: Cloudflare blocks GitHub Actions' datacenter IP
        # (403); redundant with FreeJobAlert + JagranJosh. Re-enable if running
        # from a non-blocked IP.  ("SarkariResult", govt.fetch_sarkari_result),
        ("MyGov", govt.fetch_mygov),

        # ---- Unstop (scholarships, internships, hackathons, competitions) ----
        ("Unstop-Scholarships", scholarships.fetch_unstop_scholarships),
        ("Unstop-Internships", scholarships.fetch_unstop_internships),
        ("Unstop-Hackathons", scholarships.fetch_unstop_hackathons),
        ("Unstop-Competitions", scholarships.fetch_unstop_competitions),

        # ---- Scholarships ----
        ("ScholarshipsInIndia", scholarships.fetch_scholarshipsinindia),

        # ---- Global aggregators (generic RSS) ----
        ("OpportunitiesForYouth", lambda: scholarships.fetch_generic_rss(
            "https://opportunitiesforyouth.org/feed/", "OpportunitiesForYouth")),
        ("OpportunitiesCircle", lambda: scholarships.fetch_generic_rss(
            "https://opportunitiescircle.com/feed/", "OpportunitiesCircle")),
        # OpportunityDesk DISABLED: Cloudflare blocks GitHub Actions IP (403);
        # covered by the other aggregators. ("OpportunityDesk", lambda: ...)
        ("ScholarshipRoar", lambda: scholarships.fetch_generic_rss(
            "https://scholarshiproar.com/feed/", "ScholarshipRoar")),
        ("OpportunityCell", lambda: scholarships.fetch_generic_rss(
            "https://opportunitycell.com/feed/", "OpportunityCell")),
        ("Oyaop", lambda: scholarships.fetch_generic_rss(
            "https://oyaop.com/feed/", "Oyaop")),

        # ---- Hackathons & competitions ----
        # HackerEarth DISABLED: Cloudflare blocks GitHub Actions IP (403);
        # hackathons covered by Devfolio + Unstop + Devpost.
        # ("HackerEarth", hackathons.fetch_hackerearth),
        ("Devpost", hackathons.fetch_devpost_hackathons),
        ("Codeforces", hackathons.fetch_codeforces),
        ("Devfolio", hackathons.fetch_devfolio),

        # ---- Internships ----
        ("GitHub-Simplify", internships.fetch_github_internships),
        ("GitHub-speedyapply", internships.fetch_speedyapply_intl),
        ("GitHub-NewGrad", internships.fetch_github_newgrad),
        ("foundit.in", internships.fetch_foundit),
        ("Internshala", internships.fetch_internshala),
        ("AICTE", internships.fetch_aicte_internships),

        # ---- Fellowships ----
        ("GovAI", fellowships.fetch_governance_ai),
        # ISTI DISABLED: portal is now JS-rendered — listings aren't in the
        # static HTML, so nothing to scrape. ("ISTI", fellowships.fetch_isti_portal),
    ]


def fetch_all_sources():
    """Run every registered source in isolation and return the merged list.

    A crash in one source is caught, logged, recorded for the Telegram error
    summary, and does not abort the run. Per-source counts are printed so a
    source that silently rots (drops to 0) is visible in the logs.
    """
    all_opportunities = []
    for label, fetch_fn in _build_sources():
        try:
            items = fetch_fn() or []
            all_opportunities.extend(items)
            flag = "  <-- 0 items (check source)" if not items else ""
            print(f"[COUNT] {label}: {len(items)}{flag}")
        except Exception as e:
            print(f"[ERROR] Source '{label}' crashed: {e}")
            _source_errors.append(label)
    return all_opportunities

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
    batch_size = LLM_BATCH_SIZE

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
- 9-10: Perfect fit (open-source program like GSoC/MLH/LFX, AI/ML, software, data, CS research, tech fellowship/scholarship) that an Indian 2028 undergrad can apply to (India, remote, or abroad-with-relocation).
- 6-8: Good fit (general software/engineering/tech role or program, coding hackathon, eligible tech scholarship, academy/mentorship program for pre-final-year students).
- 3-5: Weak/uncertain fit (tangentially technical, or eligibility unclear).
- 0-2: Not relevant. Score here if ANY apply: non-tech (sales/marketing/HR/content/management/case-study/journalism/MBA/medical/law); restricted to another country's citizens/residents or requires work authorization the student lacks; full-time/senior/"new grad" role needing graduation in 2025/2026/2027 or a completed degree (student graduates in 2028); needs Master's/PhD or years of experience; EXCLUSIVELY for women or reserved ONLY for SC/ST/OBC/EWS (the student is male, General category).

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

    # ---- Fetch from all sources (isolated; one failure won't stop the rest) ----
    all_opportunities = fetch_all_sources()

    # Trigger static reminders on the 1st of the month
    send_monthly_reminders()
    send_flagship_reminders()

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

    # ---- Geo/eligibility filter — ONLY for onsite jobs (internships/govt).
    #      Fellowships/scholarships/competitions/hackathons are kept worldwide
    #      because the student is open to relocation and wants global programs. ----
    before = len(all_opportunities)
    all_opportunities = [o for o in all_opportunities if not should_drop_geo(o)]
    print(f"[INFO] Removed {before - len(all_opportunities)} geo-ineligible onsite jobs "
          f"(foreign-only internships/govt). Kept {len(all_opportunities)}")

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
