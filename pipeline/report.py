"""Daily Telegram report: what was produced/posted/failed today, 7-day numbers, best video and category."""
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import analytics, gate, telegram_bot

IST = ZoneInfo("Asia/Kolkata")


def build(now=None):
    now = now or datetime.now(IST)
    today = now.strftime("%Y-%m-%d")
    week = [(now - timedelta(days=d)).strftime("%Y-%m-%d") for d in range(7)]
    records = gate.load_all()
    stats = analytics.load()

    todays = {j: (r if isinstance(r, dict) else {"status": r}) for j, r in records.items() if j.startswith(today)}
    lines = [f"📊 Tech Talk Hathim · daily report {now:%d %b %Y}", ""]
    if todays:
        for j, r in todays.items():
            title = (r.get("info") or {}).get("title") or j
            icon = {"published": "✅", "rejected": "❌", "expired": "⏭", "posting": "⏳", "failed": "🛑"}.get(r["status"], "•")
            lines.append(f"{icon} {r['status']}: {title}")
            for p, link in (r.get("platforms") or {}).items():
                lines.append(f"    {p}: {link}")
    else:
        lines.append("No videos were approved or posted today.")

    totals = defaultdict(lambda: defaultdict(int))
    by_cat = defaultdict(list)
    best = None
    for j, e in stats.items():
        if j[:10] not in week:
            continue
        views = 0
        for p, m in e.get("platforms", {}).items():
            for k in ("views", "likes", "comments", "shares", "saves"):
                totals[p][k] += m.get(k) or 0
            views += m.get("views") or 0
        cat = (e.get("info") or {}).get("category") or "Uncategorised"
        by_cat[cat].append(views)
        if not best or views > best[0]:
            best = (views, (e.get("info") or {}).get("title") or j)

    lines += ["", "Last 7 days:"]
    if totals:
        for p, t in totals.items():
            er = analytics.engagement_rate(t)
            lines.append(f"• {p}: {t['views']} views, {t['likes']} likes, {t['comments']} comments"
                         + (f", ER {er}%" if er is not None else ""))
        if best:
            lines.append(f"🏆 Top video: {best[1]} ({best[0]} views)")
        if by_cat:
            ranked = sorted(by_cat.items(), key=lambda kv: -sum(kv[1]) / len(kv[1]))
            lines.append("Categories by average views: " + ", ".join(
                f"{c} ({sum(v) // len(v)})" for c, v in ranked[:4]))
    else:
        lines.append("No numbers yet (they appear a day after the first posts).")

    problems = [j for j, r in records.items() if isinstance(r, dict) and r.get("status") in ("failed", "posting")]
    if problems:
        lines += ["", "⚠️ Needs attention: " + ", ".join(problems[-5:])]
    return "\n".join(lines)


if __name__ == "__main__":
    text = build()
    print(text)
    telegram_bot.notify(text)
