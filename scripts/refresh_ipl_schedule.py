#!/usr/bin/env python3
"""
Refresh data/ipl_2026_schedule.json from a saved Cricbuzz HTML file.

Why this exists:
    Cricbuzz's IPL series page renders fixture data via React Server
    Components AFTER initial server-side render. A curl/requests fetch
    only gets the SSR shell — the matchInfo JSON is not in it. But a
    BROWSER save (Cmd+S → Webpage HTML Only) executes JS during the page
    load, so the saved HTML contains the fully-hydrated matchInfo blocks.

    This script extracts those matchInfo blocks and writes them to the
    static schedule file the bot reads at runtime. One-shot per refresh.

When to refresh:
    - Start of an IPL season (full schedule once teams are fixtured)
    - After playoffs are scheduled (Q1 / Eliminator / Q2 / Final)
    - If matches get rescheduled mid-season due to weather/logistics

How to use:
    1. In Chrome, open
       https://www.cricbuzz.com/cricket-series/9241/indian-premier-league-2026/matches
       (replace 9241 with the current series ID if it changes between
       seasons — find it in any IPL match URL)
    2. Cmd+S → "Webpage, HTML Only" → save anywhere (e.g. ~/Downloads/)
    3. Run:  python3 scripts/refresh_ipl_schedule.py <path-to-saved.html>
    4. Inspect the printed summary, then commit data/ipl_2026_schedule.json

Tested against the 1 May 2026 schedule page (70 league-phase matches
extracted, including 12 doubleheader days).
"""

import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

UTC = timezone.utc
IST = timezone(timedelta(hours=5, minutes=30))

REPO_ROOT = Path(__file__).parent.parent
OUTPUT_PATH = REPO_ROOT / "data" / "ipl_2026_schedule.json"

# Anchor regex for each matchInfo JSON block. The HTML has them escaped
# as \"matchInfo\":{ — this matches that exact form.
_MATCHINFO_OPEN = re.compile(r'\\"matchInfo\\":\{')


def _walk_balanced(s: str, start: int) -> int:
    """Walk forward from { at `start` to find the matching }. Skip escaped
    \" strings so brace-counting isn't confused by braces inside strings."""
    depth = 0
    i = start
    in_string = False
    while i < len(s):
        c = s[i]
        if in_string:
            if c == '\\' and i + 1 < len(s):
                if s[i + 1] == '"':
                    in_string = False
                    i += 2
                    continue
                i += 2
                continue
            i += 1
        else:
            if c == '\\' and i + 1 < len(s) and s[i + 1] == '"':
                in_string = True
                i += 2
                continue
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    return i + 1
            i += 1
    return -1


def extract_ipl_schedule(html: str) -> dict:
    """Extract IPL fixtures from Cricbuzz HTML. Returns date->matches dict."""
    by_date: dict = {}
    seen_ids: set = set()

    for m in _MATCHINFO_OPEN.finditer(html):
        brace_pos = m.end() - 1
        end_pos = _walk_balanced(html, brace_pos)
        if end_pos < 0:
            continue
        raw = html[brace_pos:end_pos]
        # Unescape: \" -> ", \\ -> \, \/ -> /
        unescaped = (
            raw.replace(r'\\', '\x00')
               .replace(r'\"', '"')
               .replace(r'\/', '/')
               .replace('\x00', '\\')
        )
        try:
            info = json.loads(unescaped)
        except Exception:
            continue
        # IPL filter — series name must mention IPL
        series = (info.get("seriesName") or "").lower()
        if "indian premier league" not in series and "ipl" not in series.split():
            continue
        match_id = info.get("matchId")
        if match_id in seen_ids:
            continue
        seen_ids.add(match_id)
        start_ms = info.get("startDate")
        if not start_ms:
            continue
        try:
            dt_utc = datetime.fromtimestamp(int(start_ms) / 1000, tz=UTC)
        except (ValueError, TypeError):
            continue
        dt_ist = dt_utc.astimezone(IST)
        venue = info.get("venueInfo", {}) or {}
        date_iso = dt_ist.strftime("%Y-%m-%d")
        by_date.setdefault(date_iso, []).append({
            "match_id": match_id,
            "team1": info.get("team1", {}).get("teamName", ""),
            "team2": info.get("team2", {}).get("teamName", ""),
            "desc": info.get("matchDesc", ""),
            "venue_ground": venue.get("ground", ""),
            "venue_city": venue.get("city", ""),
            "time_ist_24": dt_ist.strftime("%H:%M"),
            "time_ist_12": dt_ist.strftime("%I:%M %p").lstrip("0"),
        })
    # Sort doubleheaders by start time
    for date_iso in by_date:
        by_date[date_iso].sort(key=lambda m: m["time_ist_24"])
    return by_date


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        print("\nUsage: python3 scripts/refresh_ipl_schedule.py <path-to-cricbuzz-html>")
        return 1

    html_path = Path(sys.argv[1]).expanduser()
    if not html_path.exists():
        print(f"FAIL: file not found: {html_path}")
        return 1

    print(f"Reading: {html_path}")
    with open(html_path, "r", encoding="utf-8", errors="ignore") as f:
        html = f.read()
    print(f"HTML size: {len(html):,} chars\n")

    by_date = extract_ipl_schedule(html)
    if not by_date:
        print("FAIL: extracted 0 matches.")
        print("Likely the HTML is from a curl/server fetch (no JS hydration)")
        print("rather than a browser save. Re-save in Chrome via Cmd+S.")
        return 1

    total = sum(len(v) for v in by_date.values())
    dates = sorted(by_date.keys())
    print(f"Extracted {total} matches across {len(dates)} dates")
    print(f"Date range: {dates[0]} -> {dates[-1]}\n")
    print("Doubleheader days:")
    for d in dates:
        if len(by_date[d]) > 1:
            print(f"  {d}: {len(by_date[d])} matches")
    print()

    manifest = {
        "season": "IPL 2026",
        "source": str(html_path.name),
        "generated_at": datetime.now(IST).isoformat(),
        "total_matches": total,
        "date_range": [dates[0], dates[-1]],
        "schedule": {d: by_date[d] for d in dates},
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"Wrote {OUTPUT_PATH}")
    print("\nNext: git add data/ipl_2026_schedule.json && git commit -m 'refresh IPL schedule'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
