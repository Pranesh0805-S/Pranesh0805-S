#!/usr/bin/env python3
"""Render a self-contained GitHub contribution summary SVG for the profile README.

Reads contribution data from GitHub's GraphQL API and writes cards/metrics-summary.svg
(override with the CARD_OUTPUT environment variable). It deliberately does NOT write
cards/activity.svg, which belongs to scripts/build_cards.py.
No third-party Python dependencies are required.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

USERNAME = "Pranesh0805-S"
OUTPUT = Path(os.environ.get("CARD_OUTPUT", "cards/metrics-summary.svg"))
API_URL = "https://api.github.com/graphql"

QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar {
        totalContributions
        weeks {
          contributionDays {
            date
            contributionCount
          }
        }
      }
    }
  }
}
"""


def fetch_contributions(token: str, start: datetime, end: datetime) -> dict:
    payload = {
        "query": QUERY,
        "variables": {
            "login": USERNAME,
            "from": start.isoformat().replace("+00:00", "Z"),
            "to": end.isoformat().replace("+00:00", "Z"),
        },
    }
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "Pranesh0805-S-profile-card-renderer",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"GitHub GraphQL returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach GitHub GraphQL: {exc.reason}") from exc

    if result.get("errors"):
        messages = "; ".join(item.get("message", "Unknown GraphQL error") for item in result["errors"])
        raise RuntimeError(f"GitHub GraphQL error: {messages}")
    user = result.get("data", {}).get("user")
    if not user:
        raise RuntimeError(f"GitHub user {USERNAME!r} was not returned.")
    return user["contributionsCollection"]


def render_svg(collection: dict, year: int, generated_at: datetime) -> str:
    days = [
        day
        for week in collection["contributionCalendar"]["weeks"]
        for day in week["contributionDays"]
    ]
    current_year_days = [
        item for item in days
        if datetime.strptime(item["date"], "%Y-%m-%d").year == year
    ]
    monthly = [0] * 12
    for item in current_year_days:
        date = datetime.strptime(item["date"], "%Y-%m-%d")
        monthly[date.month - 1] += int(item["contributionCount"])

    total = int(collection["contributionCalendar"]["totalContributions"])
    active_days = sum(1 for item in current_year_days if int(item["contributionCount"]) > 0)
    peak_day = max((int(item["contributionCount"]) for item in current_year_days), default=0)
    peak_month = max(monthly, default=0)
    max_bar = max(peak_month, 1)

    left = 50
    baseline = 220
    bar_width = 48
    gap = 38
    chart_height = 84
    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    bars = []
    for index, (name, value) in enumerate(zip(month_names, monthly)):
        x = left + index * (bar_width + gap)
        height = round((value / max_bar) * chart_height) if value else 2
        y = baseline - height
        delay = index * 0.055
        label = f"{value:,}"
        bars.append(
            f'<g class="month" style="--delay:{delay:.3f}s">'
            f'<rect x="{x}" y="{y}" width="{bar_width}" height="{height}" rx="7" '
            f'class="bar" fill="#3b82f6"><title>{escape(name)}: {label} contributions</title></rect>'
            f'<text x="{x + bar_width / 2}" y="{baseline + 22}" class="month-label" text-anchor="middle">{name}</text>'
            f'<text x="{x + bar_width / 2}" y="{max(105, y - 9)}" class="value" text-anchor="middle">{label}</text>'
            f'</g>'
        )

    stamp = generated_at.strftime("%d %b %Y · %H:%M UTC")
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1120" height="286" viewBox="0 0 1120 286" role="img" aria-labelledby="title desc">
<title id="title">GitHub contributions in {year}</title>
<desc id="desc">{total:,} total contributions across {active_days} active days. Monthly contribution bars from January through December.</desc>
<style>
  text {{ font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
  .heading {{ fill:#172033; font-size:21px; font-weight:700; }}
  .muted {{ fill:#64748b; font-size:12px; }}
  .metric {{ fill:#172033; font-size:21px; font-weight:700; }}
  .metric-label {{ fill:#64748b; font-size:10px; letter-spacing:1.4px; }}
  .month-label {{ fill:#64748b; font-size:11px; }}
  .value {{ fill:#334155; font-size:10px; }}
  .bar {{ transform-box:fill-box; transform-origin:center bottom; animation:grow .7s cubic-bezier(.2,.8,.2,1) both; animation-delay:var(--delay,0s); }}
  @keyframes grow {{ from {{ transform:scaleY(.04); opacity:.25; }} to {{ transform:scaleY(1); opacity:1; }} }}
  @media(prefers-reduced-motion:reduce) {{ .bar {{ animation:none; }} }}
</style>
<rect width="1120" height="286" rx="20" fill="#f8fafc" stroke="#e2e8f0"/>
<text x="34" y="39" class="heading">GitHub contributions · {year}</text>
<text x="1086" y="38" class="muted" text-anchor="end">LIVE PROFILE DATA</text>
<g transform="translate(34 58)">
  <rect width="210" height="53" rx="11" fill="#eff6ff"/>
  <text x="15" y="24" class="metric">{total:,}</text><text x="15" y="42" class="metric-label">TOTAL CONTRIBUTIONS</text>
  <rect x="222" width="190" height="53" rx="11" fill="#f1f5f9"/>
  <text x="237" y="24" class="metric">{active_days:,}</text><text x="237" y="42" class="metric-label">ACTIVE DAYS</text>
  <rect x="424" width="190" height="53" rx="11" fill="#fefce8"/>
  <text x="439" y="24" class="metric">{peak_day:,}</text><text x="439" y="42" class="metric-label">BEST DAY</text>
  <text x="1085" y="31" class="muted" text-anchor="end">Refreshed {escape(stamp)}</text>
</g>
<path d="M34 218H1086" stroke="#e2e8f0"/>
{''.join(bars)}
<text x="34" y="266" class="muted">Monthly totals · GitHub contribution calendar</text>
<text x="1086" y="266" class="muted" text-anchor="end">github.com/{USERNAME}</text>
</svg>
"""


def main() -> int:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("Missing GH_TOKEN (or GITHUB_TOKEN).", file=sys.stderr)
        return 1

    now = datetime.now(timezone.utc)
    start = datetime(now.year, 1, 1, tzinfo=timezone.utc)
    try:
        collection = fetch_contributions(token, start, now)
        svg = render_svg(collection, now.year, now)
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(svg, encoding="utf-8")
    except (RuntimeError, KeyError, ValueError, OSError) as exc:
        print(f"Could not render contribution card: {exc}", file=sys.stderr)
        return 1

    print(f"Rendered {OUTPUT} for {USERNAME}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
