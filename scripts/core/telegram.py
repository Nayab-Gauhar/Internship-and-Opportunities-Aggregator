import urllib.request, urllib.parse, time, os
from datetime import datetime
from core.utils import _source_errors

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')



def send_telegram(message):
    """Send a message to Telegram."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[WARN] Telegram credentials not set. Printing instead:")
        print(message)
        print()
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true"
    }).encode()

    try:
        req = urllib.request.Request(url, data=data)
        urllib.request.urlopen(req, timeout=15)
        time.sleep(1)  # Rate limit: 1 msg/sec
    except Exception as e:
        print(f"[ERROR] Telegram send failed: {e}")

def send_category(category, items):
    """Send ALL items of a category to Telegram, splitting across multiple
    messages when needed to stay under Telegram's per-message size limit."""
    emoji, label = CATEGORY_META.get(category, ("\U0001f4cc", category.title()))
    total = len(items)

    header = f"{emoji} <b>{label.upper()}</b>  ({total} new)\n" + "\u2501" * 18 + "\n"
    msg = header
    part = 1

    for i, opp in enumerate(items, 1):
        block = format_item(i, opp)
        # If adding this block would exceed the budget, flush current message first
        if len(msg) + len(block) > MAX_MSG_CHARS:
            send_telegram(msg)
            part += 1
            msg = f"{emoji} <b>{label.upper()}</b>  (contd. {part})\n" + "\u2501" * 18 + "\n" + block
        else:
            msg += block

    if msg.strip():
        send_telegram(msg)

def send_digest(relevant, total_new, total_fetched=0):
    """Group opportunities by category and send a clean digest to Telegram."""
    grouped = {}
    for opp in relevant:
        grouped.setdefault(opp["category"], []).append(opp)

    # ---- Summary header with per-category breakdown ----
    header = "\U0001f514 <b>New Opportunities for You!</b>\n"
    header += f"\U0001f4c6 <i>{datetime.now().strftime('%d %b %Y, %I:%M %p')}</i>\n\n"
    for cat in CATEGORY_ORDER:
        if cat in grouped:
            emoji, label = CATEGORY_META[cat]
            header += f"{emoji} {label}: <b>{len(grouped[cat])}</b>\n"
    header += f"\n\U0001f4ca <b>{len(relevant)}</b> relevant out of {total_new} new"
    send_telegram(header)

    # ---- All items per category (auto-split into multiple messages) ----
    for cat in CATEGORY_ORDER:
        if cat in grouped:
            send_category(cat, grouped[cat])

    # ---- Stats footer ----
    footer_parts = [f"\U0001f4ca Fetched: {total_fetched}",
                    f"New: {total_new}",
                    f"Sent: {len(relevant)}"]
    if _source_errors:
        unique_errors = list(dict.fromkeys(_source_errors))  # dedupe, preserve order
        footer_parts.append(f"\n\u26a0\ufe0f Failed: {', '.join(unique_errors[:5])}")
    send_telegram(" \u00b7 ".join(footer_parts))

def send_monthly_reminders():
    """Send a monthly static reminder for decentralized / unscrapeable portals."""
    # Only run on the 1st of the month
    if datetime.now().day != 1:
        return

    msg = (
        "\U0001f4c2 <b>Monthly Manual Check Reminder</b>\n\n"
        "Some portals are decentralized, require logins, or block automated bots. Please check these manually:\n\n"
        "• <b>DRDO</b>: Check CAIR/DYSL-AI lab sites or email them\n"
        "• <b>ISRO</b>: Check SAC, NRSC, and IIRS portals\n"
        "• <b>myScheme & NSP</b>: Check for new state/central scholarships\n"
        "• <b>PM Internship</b>: pminternship.mca.gov.in\n"
        "• <b>NITI Aayog</b>: workforindia.niti.gov.in\n"
        "• <b>IndiaAI</b>: fellowship.indiaai.gov.in\n"
        "• <b>iDEX</b>: Defence Innovation (idex.gov.in)\n"
        "• <b>Bihar SCC</b>: 7nishchay-yuvaupmission.bihar.gov.in\n"
        "• <b>Smart India Hackathon</b>: sih.gov.in"
    )
    send_telegram(msg)
