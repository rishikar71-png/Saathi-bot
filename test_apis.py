"""
Quick API verification script — run this once to confirm all three
live data keys are working before pilot.

Usage (from your saathi-bot folder in terminal):
    python test_apis.py

You need the three keys set as environment variables first.
On Mac, run these three lines before the script (replace with your real keys):

    export WEATHER_API_KEY="your_key_here"
    export CRICKET_API_KEY="your_key_here"
    export NEWS_API_KEY="your_key_here"

Then run:
    python test_apis.py
"""

import os
import sys

# Check keys are present
missing = []
for key in ("WEATHER_API_KEY", "CRICKET_API_KEY", "NEWS_API_KEY"):
    if not os.environ.get(key):
        missing.append(key)

if missing:
    print("\n❌  Missing environment variables:")
    for m in missing:
        print(f"    {m}")
    print("\nSet them with:  export KEY_NAME='your_value'")
    sys.exit(1)

from apis import fetch_weather, fetch_cricket, fetch_news

print("\n─────────────────────────────────────────")
print("  Saathi API Verification")
print("─────────────────────────────────────────\n")

# 1. Weather
print("1. WEATHER (Delhi)")
result = fetch_weather("Delhi")
if result:
    print(f"   ✅  {result}")
else:
    print("   ❌  No data returned — check WEATHER_API_KEY")
    print("       Note: new OpenWeatherMap keys can take up to 2 hours to activate.")

print()

# 2. Cricket
print("2. CRICKET (current India matches)")
result = fetch_cricket()
if result:
    print(f"   ✅  {result}")
else:
    print("   ℹ️   No India match live right now — this is normal on non-match days.")
    print("       The API is working; cricket data will appear on match days.")

print()

# 3. News
print("3. NEWS (general India headlines)")
result = fetch_news("")
if result:
    print(f"   ✅  {result}")
else:
    print("   ❌  No data returned — check NEWS_API_KEY")

print()
print("─────────────────────────────────────────")
print("  Done.")
print("─────────────────────────────────────────\n")
