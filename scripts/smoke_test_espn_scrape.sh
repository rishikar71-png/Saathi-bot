#!/usr/bin/env bash
# Smoke test for the ESPNCricinfo scraper (Bug E1''').
# Run from your Mac BEFORE pushing to Railway, to confirm:
#   1. ESPN serves us the homepage (no 403 / bot detection)
#   2. The patterns the parser relies on are present in the live HTML
#
# Usage:
#   bash scripts/smoke_test_espn_scrape.sh
#
# Pass criteria (see "RESULT:" line at the end):
#   - HTTP 200 on the curl
#   - At least one "title + startTime" pair extracted from SSR JSON
#   - Parser output (PARSED: ...) shows today's match if there is one

set -uo pipefail

UA='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
URL='https://www.espncricinfo.com/'

echo "=== Fetching $URL ==="
HTTP_CODE=$(curl -s -o /tmp/espn_homepage.html -w "%{http_code}" \
  -H "User-Agent: $UA" \
  -H "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8" \
  -H "Accept-Language: en-US,en;q=0.5" \
  -H "Accept-Encoding: gzip, deflate, br" \
  -H "DNT: 1" \
  -H "Connection: keep-alive" \
  -H "Upgrade-Insecure-Requests: 1" \
  -H "Sec-Fetch-Dest: document" \
  -H "Sec-Fetch-Mode: navigate" \
  -H "Sec-Fetch-Site: none" \
  -H "Sec-Fetch-User: ?1" \
  --compressed \
  "$URL")
echo "HTTP code: $HTTP_CODE"
echo "Bytes:     $(wc -c < /tmp/espn_homepage.html)"
echo

if [ "$HTTP_CODE" != "200" ]; then
  echo "FAIL: expected HTTP 200, got $HTTP_CODE"
  exit 1
fi

echo "=== Pass A: SSR JSON 'title' + 'startTime' pairs (the regex parser uses) ==="
python3 << 'PYEOF'
import re
html = open('/tmp/espn_homepage.html').read()
pat = re.compile(
    r'"title":"([^"]{1,50}:\s*([^"]+?) v ([^"]+?) at ([^,"]+),\s*(\w+)\s+(\d+),\s*(\d{4}))"'
    r'[^}]{0,800}?'
    r'"startTime":"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)"'
)
hits = pat.findall(html)
print(f"Pass A hits: {len(hits)}")
for h in hits[:10]:
    print(f"  - {h[0]} | startTime={h[7]}")
if len(hits) == 0:
    print("FAIL: no title+startTime pairs found. ESPN may have changed JSON layout.")
PYEOF
echo

echo "=== Today's IPL match per parser (if any) ==="
python3 << PYEOF
import sys
sys.path.insert(0, '$(pwd)')
from datetime import datetime, timezone, timedelta
IST = timezone(timedelta(hours=5, minutes=30))
today = datetime.now(IST).strftime('%Y-%m-%d')
print(f'today_ist = {today}')
from apis import _parse_espn_homepage
html = open('/tmp/espn_homepage.html').read()
result = _parse_espn_homepage(html, today)
if result:
    print('PARSED:')
    print(result)
else:
    print('No IPL match found for today.')
    print('(May be correct — IPL has rest days. Cross-check with espncricinfo.com homepage.)')
PYEOF
echo

echo "=== RESULT ==="
echo "If 'PARSED:' line above shows today's match with start time -> ship it."
echo "If 'No IPL match found' but you can see one on espncricinfo.com -> regex needs work."
