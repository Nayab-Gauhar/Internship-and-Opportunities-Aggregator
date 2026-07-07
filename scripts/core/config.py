"""
Central configuration for the Opportunity Bot.
--------------------------------------------------
All tunable knobs live here so there is a single source of truth. Every value
can be overridden via environment variables (useful for GitHub Actions secrets
and for other users who want to reuse the bot without editing code).
"""

import os

# ============================================================
# USER PROFILE  (the LLM uses this to judge relevance)
# ============================================================
# Override with the USER_PROFILE env var to reuse the bot for someone else.
USER_PROFILE = os.environ.get("USER_PROFILE", """
- B.Tech Computer Science student in India, GRADUATING IN 2028 (entering 3rd year / 5th semester).
- Nationality: Indian. Gender: Male. Category: General.
- Based in Bhopal, India, but OPEN TO RELOCATION anywhere (India or abroad) for a good opportunity.
- Interests: AI/ML, Software Development, DSA, Web Development, and ESPECIALLY OPEN-SOURCE
  contribution programs.
- Skills: Python, C++, Java, SQL, HTML, CSS, JavaScript.

- Actively wants a BROAD range of opportunities:
  * Open-source mentorship programs (GSoC, MLH Fellowship, LFX Mentorship, C4GT, GSSoC, Summer of Bitcoin)
  * Early-career / "academy" programs for pre-final-year students (Microsoft Engage, Goldman Sachs
    Engineering, JPMorgan Code for Good)
  * Software / AI-ML / Data internships (Summer 2027, remote, or off-cycle)
  * Student fellowships, tech scholarships, research programs, summer schools
  * Hackathons and coding competitions

- ELIGIBILITY RULES (used to reject listings that do not fit):
  * KEEP anything open to Indian / international / "open to all" applicants — in India, remote,
    OR abroad (the student is willing to relocate).
  * KEEP international fellowships, scholarships, summer schools, research and open-source programs
    that accept international or Indian applicants.
  * REJECT roles restricted to another country's citizens/residents (e.g. "US citizens only",
    "must have UK work authorization").
  * REJECT full-time / senior / "new grad" roles that require graduating in 2025/2026/2027 or an
    already-completed degree — the student graduates in 2028 and wants internships/programs, not
    full-time jobs.
  * REJECT roles requiring a Master's / PhD or years of work experience.
  * The student is MALE / GENERAL category, so programs EXCLUSIVELY for women, or reserved ONLY for
    SC/ST/OBC/EWS, are NOT eligible — do not surface women-only or reserved-category-only
    scholarships/fellowships.

- NOT interested in: MBA / management, sales / marketing / HR, business / case-study / strategy
  competitions, journalism, content writing, finance / consulting, medical, law, agriculture,
  arts / humanities-only.
""").strip()

# ============================================================
# GROQ LLM SETTINGS
# ============================================================
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()

# Update here if the model gets deprecated (see https://console.groq.com/docs/models)
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile").strip()

# Minimum LLM relevance score (0-10) for an opportunity to be sent. Higher = stricter.
MIN_RELEVANCE_SCORE = int(os.environ.get("MIN_RELEVANCE_SCORE", "6"))

# How many opportunities to score per LLM request (keeps prompts small & cheap).
LLM_BATCH_SIZE = int(os.environ.get("LLM_BATCH_SIZE", "15"))

# ============================================================
# CATEGORIES
# ============================================================
# Categories that are always relevant to the user — these skip the LLM entirely
# to conserve the free-tier quota. ONLY coding hackathons are blanket-approved;
# competitions / scholarships / fellowships are relevance-checked because many
# are non-tech (management case comps, climate/journalism fellowships, etc.).
AUTO_APPROVE_CATEGORIES = {"HACKATHON"}

# ============================================================
# DEDUP / SEEN STORE
# ============================================================
# Absolute path to the dedup store (repo_root/data/seen.json). Computed from this
# file's location so it is correct regardless of the current working directory.
SEEN_FILE = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "data", "seen.json")
)

# Prune seen entries older than this many seconds (30 days).
SEEN_MAX_AGE = 30 * 86400
