"""
apis.py — Module 18

Fetches real-world data for the morning briefing: weather, cricket, news.

All three functions follow the same contract:
  - Return a plain-text string of raw data on success.
  - Return None on any failure (missing key, network error, empty response).
  - Never raise — caller (rituals.py) treats None as "skip this element".

The data returned here is RAW. It is always passed through the wrap functions
in rituals.py (wrap_weather, wrap_cricket, wrap_news) before being shown to
any senior. Those wrap functions call DeepSeek to convert raw data into a warm,
contextual sentence. The senior never sees a raw API response.

Env vars required (add to Railway):
  WEATHER_API_KEY  — from openweathermap.org (free tier)
  CRICKET_API_KEY  — from api.cricapi.com (free tier)
  NEWS_API_KEY     — from newsapi.org (free tier)

If any key is absent, the corresponding fetch returns None and the morning
briefing simply omits that element. No crash, no error shown to user.

In-memory cache (30-minute TTL) prevents duplicate API calls when multiple
users share the same morning check-in time. Cache keys:
  weather:{city_lower}
  cricket (no key — one global call per cycle)
  news:{keyword_lower}
"""

import logging
import os
import re
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Simple in-memory cache — avoids hammering APIs when many users share the
# same morning check-in time. TTL: 30 minutes (1800 seconds).
# ---------------------------------------------------------------------------

_CACHE: dict[str, tuple[float, Optional[str]]] = {}
_CACHE_TTL = 1800  # seconds


def _cache_get(key: str) -> tuple[bool, Optional[str]]:
    """Return (hit, value). hit=False means expired or missing."""
    entry = _CACHE.get(key)
    if entry is None:
        return False, None
    ts, value = entry
    if time.time() - ts > _CACHE_TTL:
        del _CACHE[key]
        return False, None
    return True, value


def _cache_set(key: str, value: Optional[str]) -> None:
    _CACHE[key] = (time.time(), value)


# ---------------------------------------------------------------------------
# Shared city alias map — used by BOTH onboarding (to canonicalize user input
# before storing) AND fetch_weather (to resolve OWM 404s on short names).
#
# Added 22 Apr 2026. Previously the map lived inside fetch_weather(); seniors
# who typed short forms like "Mum" / "Del" during onboarding got their raw
# input stored to the DB, which then showed up in the morning briefing ("in
# Mum…") and caused OWM weather lookups to fail.
#
# Keys are lowercase. Values are the canonical display name (NO country-code
# suffix — that gets appended only for OWM retries).
#
# Keep extending this as pilot surfaces more short forms / old names.
# ---------------------------------------------------------------------------
CITY_ALIASES: dict[str, str] = {
    # --- India ---
    # Mumbai
    "mum": "Mumbai", "mumbai": "Mumbai", "bombay": "Mumbai", "bby": "Mumbai",
    # Delhi
    "del": "New Delhi", "delhi": "New Delhi", "new delhi": "New Delhi",
    "ndl": "New Delhi", "new-delhi": "New Delhi",
    # Bengaluru
    "blr": "Bengaluru", "bengaluru": "Bengaluru", "bangalore": "Bengaluru",
    "blore": "Bengaluru", "banglore": "Bengaluru",
    # Hyderabad
    "hyd": "Hyderabad", "hyderabad": "Hyderabad", "hydrabad": "Hyderabad",
    # Chennai
    "chn": "Chennai", "chennai": "Chennai", "madras": "Chennai",
    # Kolkata
    "kol": "Kolkata", "kolkata": "Kolkata", "calcutta": "Kolkata", "cal": "Kolkata",
    # Pune
    "pune": "Pune", "poona": "Pune", "pnq": "Pune",
    # Ahmedabad
    "ahd": "Ahmedabad", "ahmedabad": "Ahmedabad", "amdavad": "Ahmedabad",
    "amd": "Ahmedabad",
    # Jaipur
    "jpr": "Jaipur", "jaipur": "Jaipur",
    # Chandigarh
    "chd": "Chandigarh", "chandigarh": "Chandigarh",
    # Gurugram / Gurgaon
    "ggn": "Gurugram", "gurugram": "Gurugram", "gurgaon": "Gurugram",
    # Noida
    "noida": "Noida",
    # Lucknow
    "lko": "Lucknow", "lucknow": "Lucknow",
    # A few more commonly-named Indian metros for safety.
    "indore": "Indore", "bhopal": "Bhopal", "nagpur": "Nagpur",
    "kochi": "Kochi", "cochin": "Kochi", "trivandrum": "Thiruvananthapuram",
    "thiruvananthapuram": "Thiruvananthapuram", "coimbatore": "Coimbatore",
    "visakhapatnam": "Visakhapatnam", "vizag": "Visakhapatnam",
    "surat": "Surat", "vadodara": "Vadodara", "baroda": "Vadodara",
    "patna": "Patna", "ranchi": "Ranchi", "bhubaneswar": "Bhubaneswar",
    "guwahati": "Guwahati", "dehradun": "Dehradun",
    "shimla": "Shimla", "simla": "Shimla",
    "goa": "Panaji", "panaji": "Panaji", "panjim": "Panaji",

    # --- Diaspora (added 22 Apr 2026 for pilot) ---
    # USA — West Coast
    "la": "Los Angeles", "los angeles": "Los Angeles", "l.a.": "Los Angeles",
    "la ca": "Los Angeles", "losangeles": "Los Angeles",
    "sf": "San Francisco", "san francisco": "San Francisco",
    "san fran": "San Francisco", "bay area": "San Francisco", "sfo": "San Francisco",
    "seattle": "Seattle", "sea": "Seattle",
    # USA — East Coast
    "ny": "New York", "nyc": "New York", "new york": "New York",
    "new york city": "New York", "ny city": "New York", "newyork": "New York",
    "boston": "Boston", "bos": "Boston",
    "dc": "Washington", "washington": "Washington", "washington dc": "Washington",
    "washington d.c.": "Washington", "washington, dc": "Washington",
    # USA — Central / South
    "chicago": "Chicago", "chi": "Chicago", "chitown": "Chicago",
    "dallas": "Dallas", "dfw": "Dallas",
    "houston": "Houston", "hou": "Houston",
    "atlanta": "Atlanta", "atl": "Atlanta",
    # Canada
    "toronto": "Toronto", "to": "Toronto", "yyz": "Toronto",
    "vancouver": "Vancouver", "van": "Vancouver", "yvr": "Vancouver",
    # UK
    "london": "London", "ldn": "London", "lon": "London",
    # Gulf
    "dubai": "Dubai", "dxb": "Dubai",
    "abu dhabi": "Abu Dhabi", "auh": "Abu Dhabi",
    "doha": "Doha",
    # Southeast / East Asia
    "singapore": "Singapore", "sg": "Singapore", "sgp": "Singapore",
    "hong kong": "Hong Kong", "hk": "Hong Kong", "hkg": "Hong Kong",
    # Australia / NZ
    "syd": "Sydney", "sydney": "Sydney",
    "mel": "Melbourne", "melbourne": "Melbourne", "mlb": "Melbourne",
    "auckland": "Auckland", "akl": "Auckland",
    # Europe (common diaspora)
    "paris": "Paris", "berlin": "Berlin", "frankfurt": "Frankfurt",
    "amsterdam": "Amsterdam", "ams": "Amsterdam",
    "zurich": "Zurich", "zrh": "Zurich",
}


# ---------------------------------------------------------------------------
# City → IANA timezone map. Keys MUST match canonical names in CITY_ALIASES
# values (case-sensitive). Missing keys fall back to IST in get_iana_timezone.
#
# Added 22 Apr 2026 for diaspora pilot users. Before this, deepseek.py had
# a hardcoded 5.5 offset for all Indian cities and rituals.py compared every
# user's check-in time against a global IST clock — so an LA senior's 8am
# briefing would fire at 8:00 IST = 7:30pm PDT the previous night.
# ---------------------------------------------------------------------------

CITY_TIMEZONE: dict[str, str] = {
    # India — all share IST.
    "Mumbai": "Asia/Kolkata",
    "New Delhi": "Asia/Kolkata",
    "Bengaluru": "Asia/Kolkata",
    "Hyderabad": "Asia/Kolkata",
    "Chennai": "Asia/Kolkata",
    "Kolkata": "Asia/Kolkata",
    "Pune": "Asia/Kolkata",
    "Ahmedabad": "Asia/Kolkata",
    "Jaipur": "Asia/Kolkata",
    "Chandigarh": "Asia/Kolkata",
    "Gurugram": "Asia/Kolkata",
    "Noida": "Asia/Kolkata",
    "Lucknow": "Asia/Kolkata",
    "Indore": "Asia/Kolkata",
    "Bhopal": "Asia/Kolkata",
    "Nagpur": "Asia/Kolkata",
    "Kochi": "Asia/Kolkata",
    "Thiruvananthapuram": "Asia/Kolkata",
    "Coimbatore": "Asia/Kolkata",
    "Visakhapatnam": "Asia/Kolkata",
    "Surat": "Asia/Kolkata",
    "Vadodara": "Asia/Kolkata",
    "Patna": "Asia/Kolkata",
    "Ranchi": "Asia/Kolkata",
    "Bhubaneswar": "Asia/Kolkata",
    "Guwahati": "Asia/Kolkata",
    "Dehradun": "Asia/Kolkata",
    "Shimla": "Asia/Kolkata",
    "Panaji": "Asia/Kolkata",

    # USA
    "Los Angeles": "America/Los_Angeles",
    "San Francisco": "America/Los_Angeles",
    "Seattle": "America/Los_Angeles",
    "New York": "America/New_York",
    "Boston": "America/New_York",
    "Washington": "America/New_York",
    "Atlanta": "America/New_York",
    "Chicago": "America/Chicago",
    "Dallas": "America/Chicago",
    "Houston": "America/Chicago",

    # Canada
    "Toronto": "America/Toronto",
    "Vancouver": "America/Vancouver",

    # UK / Europe
    "London": "Europe/London",
    "Paris": "Europe/Paris",
    "Berlin": "Europe/Berlin",
    "Frankfurt": "Europe/Berlin",
    "Amsterdam": "Europe/Amsterdam",
    "Zurich": "Europe/Zurich",

    # Gulf
    "Dubai": "Asia/Dubai",
    "Abu Dhabi": "Asia/Dubai",
    "Doha": "Asia/Qatar",

    # SE / East Asia
    "Singapore": "Asia/Singapore",
    "Hong Kong": "Asia/Hong_Kong",

    # Australia / NZ
    "Sydney": "Australia/Sydney",
    "Melbourne": "Australia/Melbourne",
    "Auckland": "Pacific/Auckland",
}

_IST_TZ = "Asia/Kolkata"


def canonicalize_city(raw: str) -> str:
    """
    Normalise a user-supplied city string into a canonical display name.
    Used by onboarding at capture time so the DB never stores "Mum" or "Del".

    Falls back to title-case of the raw input if the city isn't in our alias
    map — so unknown cities still round-trip cleanly, just without weather
    reliability until we add them. Callers should log a warning in that case.
    """
    if not raw:
        return ""
    key = raw.strip().lower()
    if not key:
        return ""
    return CITY_ALIASES.get(key, raw.strip().title())


def get_iana_timezone(city: str) -> str:
    """
    Return the IANA timezone string for a city. Accepts either canonical form
    ("Mumbai", "New York") or raw user input ("mum", "NY").

    Falls back to 'Asia/Kolkata' (IST) if the city is unknown — this preserves
    the pre-22-Apr-2026 behaviour for Indian users while giving diaspora users
    correct local clocks.
    """
    if not city:
        return _IST_TZ
    # Try canonical match first, then alias resolution.
    if city in CITY_TIMEZONE:
        return CITY_TIMEZONE[city]
    canonical = canonicalize_city(city)
    return CITY_TIMEZONE.get(canonical, _IST_TZ)


# ---------------------------------------------------------------------------
# Weather — OpenWeatherMap current conditions
# Docs: https://openweathermap.org/current
# ---------------------------------------------------------------------------

_OWM_URL = "https://api.openweathermap.org/data/2.5/weather"


def fetch_weather(city: str) -> Optional[str]:
    """
    Fetch current weather for the given city from OpenWeatherMap.

    Returns a brief plain-English description of conditions suitable for
    passing to wrap_weather(). Example:
        "Mumbai: 32°C, hazy sunshine, humidity 78%, feels like 36°C"

    Returns None if the API key is missing, the city isn't found, or the
    call fails for any reason.
    """
    api_key = os.environ.get("WEATHER_API_KEY")
    if not api_key:
        return None  # Key not configured — skip silently

    city = city.strip()
    if not city:
        return None

    cache_key = f"weather:{city.lower()}"
    hit, cached = _cache_get(cache_key)
    if hit:
        logger.debug("APIS | weather cache hit | city=%s", city)
        return cached

    def _owm_get(q: str):
        return requests.get(
            _OWM_URL,
            params={"q": q, "appid": api_key, "units": "metric", "lang": "en"},
            timeout=4,
        )

    try:
        resp = _owm_get(city)

        # If OWM 404s, retry with the canonical form + ",IN" country suffix.
        # Canonicalization uses the shared CITY_ALIASES map so short forms
        # like "Mum" or "Del" that survived in old DB rows still resolve.
        if resp.status_code == 404:
            canonical = CITY_ALIASES.get(city.lower(), city)
            retry_q = f"{canonical},IN"
            logger.info("APIS | weather 404 for '%s', retrying as '%s'", city, retry_q)
            resp = _owm_get(retry_q)

        if not resp.ok:
            logger.warning(
                "APIS | weather API error | city=%s | status=%d | %s",
                city, resp.status_code, resp.text[:100],
            )
            _cache_set(cache_key, None)
            return None

        data = resp.json()
        temp = round(data["main"]["temp"])
        feels_like = round(data["main"]["feels_like"])
        humidity = data["main"]["humidity"]
        description = data["weather"][0]["description"].capitalize()

        result = (
            f"{city}: {temp}°C, {description}, "
            f"humidity {humidity}%, feels like {feels_like}°C"
        )
        _cache_set(cache_key, result)
        logger.info("APIS | weather fetched | city=%s | %s", city, result)
        return result

    except Exception as e:
        logger.warning("APIS | weather fetch failed | city=%s | %s", city, e)
        _cache_set(cache_key, None)
        return None


# ---------------------------------------------------------------------------
# Cricket — CricAPI current matches
# Docs: https://www.cricapi.com/
# Free tier: 100 calls/day — sufficient for the 20-user pilot.
# ---------------------------------------------------------------------------

_CRICAPI_URL = "https://api.cricapi.com/v1/currentMatches"
# /v1/matches: full schedule including upcoming/scheduled matches that
# /currentMatches excludes (e.g. tonight's 19:30 IST IPL game when queried
# in the morning before it has started). Same auth, same response shape.
# Fixes Bug E (29 Apr): senior asks "aaj cricket?" at 14:00 IST → /currentMatches
# returns Match 40 (yesterday) but not Match 41 (tonight) → bot says "no match today".
_CRICAPI_MATCHES_URL = "https://api.cricapi.com/v1/matches"

# Teams to match: India internationals + all 10 IPL franchises.
# IPL runs April–May — without this, all IPL matches were silently excluded.
_INDIA_TEAM_KEYWORDS = {"india", "ind"}

_IPL_TEAM_ALIASES = {
    # Abbreviations
    "mi", "csk", "rcb", "kkr", "lsg", "gt", "pbks", "rr", "dc", "srh",
    # Full names (lowercase for substring match against match name / team strings)
    "mumbai indians", "chennai super kings",
    "royal challengers bengaluru", "royal challengers bangalore",
    "lucknow super giants", "gujarat titans", "kolkata knight riders",
    "punjab kings", "rajasthan royals", "delhi capitals", "sunrisers hyderabad",
}

# Combined set used by _find_india_match
_TRACKED_TEAM_KEYWORDS = _INDIA_TEAM_KEYWORDS | _IPL_TEAM_ALIASES

# Token-aware classification of tracked keywords.
# Bug E1 fix (30 Apr 2026): the previous `kw in text` substring match
# treated `rr` (Rajasthan Royals) as a hit inside "wa**rr**iors", so
# Guyana Amazon Warriors (CPL) was surfaced as a tracked match.
#
# Rule:
#   - Single-token keywords ≤4 chars (mi, csk, rr, dc, gt, ind, etc.)
#     must match a whole word boundary, not a substring. Use re.fullmatch
#     against tokens parsed out of the text.
#   - Multi-word or longer keywords (≥5 chars; "india", "mumbai indians",
#     "rajasthan royals") match as substrings — collisions extremely
#     unlikely at this length.
_TRACKED_ABBREV: set = {
    kw for kw in _TRACKED_TEAM_KEYWORDS
    if len(kw) <= 4 and " " not in kw
}
_TRACKED_FULLNAME: set = _TRACKED_TEAM_KEYWORDS - _TRACKED_ABBREV

# Token-split: any non-letter run separates tokens.
_TOKEN_SPLIT_RE = re.compile(r"[^a-z]+")


def _is_tracked_team(text: str) -> bool:
    """Return True if `text` references India or any IPL team.

    Short abbreviations (`mi`, `rr`, `gt`, `dc`, `csk`, `ind`, ...) match
    only when they appear as whole tokens — `rr` will NOT match "warriors".
    Longer names (`india`, `mumbai indians`, `rajasthan royals`) match as
    case-insensitive substrings; collision risk at length ≥5 is negligible.
    """
    text_lower = text.lower()
    # Substring pass — full names
    for kw in _TRACKED_FULLNAME:
        if kw in text_lower:
            return True
    # Token pass — short abbreviations
    tokens = {tok for tok in _TOKEN_SPLIT_RE.split(text_lower) if tok}
    return bool(tokens & _TRACKED_ABBREV)


def fetch_cricket() -> Optional[str]:
    """
    Fetch the current or most recent India match from CricAPI.

    Returns a plain-text match summary suitable for passing to wrap_cricket().
    Example:
        "India vs Australia (T20I) — India 186/4 after 18 overs, live at MCG"

    Returns None if:
      - No API key configured
      - No India match found in current matches
      - Any API or network failure
    """
    api_key = os.environ.get("CRICKET_API_KEY")
    if not api_key:
        return None

    cache_key = "cricket:current"
    hit, cached = _cache_get(cache_key)
    if hit:
        logger.debug("APIS | cricket cache hit")
        return cached

    matches: list = []
    seen_ids: set = set()

    def _merge_from(url: str, label: str) -> None:
        """Fetch matches from `url` and append non-duplicate items to matches."""
        try:
            resp = requests.get(
                url,
                params={"apikey": api_key, "offset": 0},
                timeout=4,
            )
            if not resp.ok:
                logger.warning(
                    "APIS | cricket %s API error | status=%d | %s",
                    label, resp.status_code, resp.text[:100],
                )
                return
            data = resp.json()
            if data.get("status") != "success":
                logger.debug("APIS | cricket %s non-success status: %s", label, data.get("status"))
                return
            for m in data.get("data", []):
                mid = m.get("id")
                if mid and mid in seen_ids:
                    continue
                if mid:
                    seen_ids.add(mid)
                matches.append(m)
        except Exception as e:
            logger.warning("APIS | cricket %s fetch failed | %s", label, e)

    try:
        # Pass 1: /currentMatches — captures live + recently completed.
        _merge_from(_CRICAPI_URL, "currentMatches")
        # Pass 2: /matches — captures upcoming/scheduled matches that pass 1 excludes.
        # This is the Bug E fix: today's not-yet-started match shows up here.
        _merge_from(_CRICAPI_MATCHES_URL, "matches")

        if matches:
            all_names = [m.get("name", "?") for m in matches[:10]]
            logger.info(
                "APIS | cricket merged matches (%d total) | %s",
                len(matches), " | ".join(all_names),
            )
        else:
            logger.info("APIS | cricket: both endpoints returned 0 matches")
            _cache_set(cache_key, None)
            return None

        india_match = _find_india_match(matches)

        _cache_set(cache_key, india_match)
        if india_match:
            logger.info("APIS | cricket fetched | %s", india_match)
        else:
            logger.info("APIS | cricket fetched | no India match today")
        return india_match

    except Exception as e:
        logger.warning("APIS | cricket fetch wrapper failed | %s", e)
        _cache_set(cache_key, None)
        return None


def _parse_match_date(raw: str) -> str:
    """
    Normalise CricAPI date strings to "YYYY-MM-DD" for comparison.

    Handles the formats seen from CricAPI free tier:
      "2026-04-15"              → "2026-04-15"
      "2026-04-15T14:00:00"     → "2026-04-15"
      "2026-04-15 14:00:00"     → "2026-04-15"
      "15-04-2026"              → "2026-04-15"
      "15 Apr 2026"             → "2026-04-15"
      "Apr 15, 2026"            → "2026-04-15"

    Returns "" if none of the formats match.
    """
    from datetime import datetime
    raw = raw.strip()
    # Fast path: already ISO format (with or without time component)
    if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
        return raw[:10]
    # Try common non-ISO formats
    for fmt in ("%d-%m-%Y", "%d %b %Y", "%b %d, %Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw[:20], fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return ""


def _find_india_match(matches: list) -> Optional[str]:
    """
    Scan the matches list for India internationals OR any IPL match.

    Filters strictly by today's IST date so yesterday's results are never
    presented as today's news. Returns a summary prefixed with match state:

        LIVE NOW — <details>           match in progress today
        TODAY (upcoming) — <details>   scheduled today, not yet started
        COMPLETED TODAY — <details>    finished today
        UPCOMING — <details>           next match (future date), only
                                        if nothing is happening today

    Returns None if no tracked match found at all.
    """
    from datetime import datetime, timezone, timedelta
    IST = timezone(timedelta(hours=5, minutes=30))
    today_ist = datetime.now(IST).strftime("%Y-%m-%d")

    today_matches = []      # (match, started, ended) for matches dated today
    upcoming_matches = []   # matches with a future date
    undated_matches = []    # tracked matches with no parseable date

    for match in matches:
        name = (match.get("name") or "").lower()
        teams = match.get("teams", [])
        team_names = " ".join(t.lower() for t in teams)

        # Token-aware tracked-team match (Bug E1 fix). Substring matching
        # let "rr" (Rajasthan Royals abbrev) hit "wa**rr**iors" — so any
        # CPL/BBL "Warriors" team falsely registered as IPL.
        if not (_is_tracked_team(name) or _is_tracked_team(team_names)):
            continue

        # Log the raw date fields for the first tracked match so we can verify
        # CricAPI is returning a parseable format (debug aid, logged once per call).
        if not today_matches and not upcoming_matches and not undated_matches:
            logger.info(
                "APIS | cricket tracked match | name=%r | raw_date=%r | raw_dtGMT=%r",
                match.get("name", ""), match.get("date", ""), match.get("dateTimeGMT", ""),
            )

        # CricAPI free tier returns dates in inconsistent formats:
        #   "YYYY-MM-DD", "YYYY-MM-DDThh:mm:ss", "YYYY-MM-DD hh:mm:ss",
        #   "dd-mm-YYYY", "dd MMM YYYY", or missing entirely.
        # _parse_match_date() normalises all to "YYYY-MM-DD" for comparison.
        raw_date = match.get("date") or match.get("dateTimeGMT") or ""
        match_date = _parse_match_date(raw_date) if raw_date else ""

        match_started = bool(match.get("matchStarted", False))
        match_ended   = bool(match.get("matchEnded", False))

        if match_date == today_ist:
            today_matches.append((match, match_started, match_ended))
        elif match_date > today_ist:
            upcoming_matches.append(match)
        elif not match_date:
            # No usable date — bucket separately so we can fall back to it
            undated_matches.append((match, match_started, match_ended))
        # Past dates (match_date < today_ist) are silently skipped.

    # Priority order: live today > upcoming today > next scheduled (future)
    for match, started, ended in today_matches:
        summary = _format_match_summary(match)
        if started and not ended:
            return f"LIVE NOW — {summary}"
        elif not started:
            return f"TODAY (upcoming) — {summary}"
        else:
            return f"COMPLETED TODAY — {summary}"

    # Nothing today — surface the next scheduled tracked match, but only
    # if it's within UPCOMING_HORIZON_DAYS. Bug E1 (30 Apr 2026): a July 31
    # match surfaced on April 30 ("aaj cricket?" → "31 July ko hai") because
    # there was no date ceiling. A senior asking about cricket today does
    # not benefit from hearing about a match three months out.
    UPCOMING_HORIZON_DAYS = 7
    today_dt = datetime.strptime(today_ist, "%Y-%m-%d").date()
    for match in upcoming_matches:
        raw_date = match.get("date") or match.get("dateTimeGMT") or ""
        match_date_str = _parse_match_date(raw_date) if raw_date else ""
        if not match_date_str:
            continue
        try:
            match_dt = datetime.strptime(match_date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        days_out = (match_dt - today_dt).days
        if 0 < days_out <= UPCOMING_HORIZON_DAYS:
            return f"UPCOMING — {_format_match_summary(match)}"
        else:
            logger.debug(
                "APIS | cricket: skipping upcoming match %s days out: %s",
                days_out, match.get("name", ""),
            )

    # Fallback: CricAPI didn't return a parseable date (common on free tier).
    # Show the first undated tracked match rather than returning None.
    # This is better than silently saying "no cricket today" when IPL is live.
    if undated_matches:
        match, started, ended = undated_matches[0]
        summary = _format_match_summary(match)
        if started and not ended:
            return f"LIVE NOW — {summary}"
        elif ended:
            return f"RECENT — {summary}"
        else:
            return f"SCHEDULED — {summary}"

    return None


def _format_match_summary(match: dict) -> str:
    """Build a human-readable one-line summary for a single match dict."""
    match_name = match.get("name", "India match")
    match_type = (match.get("matchType") or "").upper()
    status     = match.get("status", "")
    venue      = match.get("venue", "")

    score_parts = []
    for score_obj in match.get("score", []):
        inning   = score_obj.get("inning", "")
        runs     = score_obj.get("r", "")
        wickets  = score_obj.get("w", "")
        overs    = score_obj.get("o", "")
        if runs != "" and wickets != "":
            score_parts.append(f"{inning}: {runs}/{wickets} ({overs} ov)")

    score_str = " | ".join(score_parts) if score_parts else ""

    parts = [match_name]
    if match_type:
        parts[0] += f" ({match_type})"
    if score_str:
        parts.append(score_str)
    if status:
        parts.append(status)
    if venue:
        parts.append(f"at {venue}")

    return " — ".join(parts)


# ---------------------------------------------------------------------------
# News — RSS-first approach (no API key needed) + NewsAPI fallback
#
# Primary: public RSS feeds from The Hindu and NDTV (India-only, reliable).
# Fallback: NewsAPI top headlines (free tier, 100 calls/day).
#
# Why RSS primary: NewsAPI's free developer plan is unreliable from
# production servers (rate limits, domain blocks). RSS feeds are public,
# need no key, and are consistently available from any server.
#
# Uses Python's built-in xml.etree.ElementTree — no extra dependencies.
# ---------------------------------------------------------------------------

_RSS_FEEDS_INDIA = [
    # Reuters India — primary. Clean wire copy, national scope, no clickbait.
    "https://feeds.reuters.com/reuters/INtopNews",
    # Hindustan Times top stories — broad national coverage, less South India bias than The Hindu
    "https://www.hindustantimes.com/feeds/rss/topstories/rssfeed.xml",
    # NDTV India — reliable national fallback
    "https://feeds.feedburner.com/ndtvnews-india-news",
    # TOI Top Stories — broadest, last resort (noisiest)
    "https://timesofindia.indiatimes.com/rssfeeds/296589292.cms",
]

# World / international news feeds — used when user asks about global or country-specific news.
_RSS_FEEDS_WORLD = [
    # BBC World — gold standard international coverage, public RSS, no key needed
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    # Reuters World — reliable wire copy
    "https://feeds.reuters.com/reuters/worldNews",
    # BBC News top stories — includes world + UK as fallback
    "https://feeds.bbci.co.uk/news/rss.xml",
]

# Cricket-specific RSS feeds — for analysis, previews, post-match reports.
# Used by fetch_cricket_news() for queries about cricket NEWS (not live scores).
# Live scores and schedule still come from CricAPI via fetch_cricket().
#
# IMPORTANT — these URLs are best-effort candidates and need curl-verification
# on first deploy. The RSS fetcher already skips bad URLs gracefully (timeout,
# 404, parse error) and tries the next one in order. Keep more than one feed
# in this list at all times so a single broken URL doesn't take cricket news
# down completely.
#
# Verification command (run from any shell with internet access):
#     for u in <urls below>; do echo "==> $u"; curl -sS -o /dev/null -w "%{http_code}\n" "$u"; done
# Expected: 200 for the working ones. Replace any 4xx/5xx with a known-good URL
# from feedspot.com/cricket_rss_feeds or the publisher's RSS hub.
_RSS_FEEDS_CRICKET = [
    # ESPNCricinfo — quality analysis, IPL coverage, post-match reports.
    # Several candidate paths from public RSS directories — fetcher tries each.
    "https://www.espncricinfo.com/rss/content/story/feeds/0.xml",
    "https://static.espncricinfo.com/rss/livescores.xml",
    # Cricbuzz — broader IPL coverage, pre-match previews
    "https://www.cricbuzz.com/rss/cricket-features-rss",
    "https://www.cricbuzz.com/api/cricbuzz/rss/cricket",
    # NDTV Sports cricket — backstop. Always reachable, broad cricket coverage.
    "https://feeds.feedburner.com/ndtvsports-cricket",
]

# Regex to detect country/region names in a world news query
_WORLD_COUNTRY_RE = re.compile(
    r"\b(usa|america|american|u\.s\.|united states|"
    r"uk|britain|england|europe|european|eu|"
    r"china|russia|ukraine|france|germany|japan|korea|"
    r"israel|middle east|africa|australia|canada|pakistan|"
    r"iran|turkey|brazil|mexico)\b",
    re.IGNORECASE,
)

# Normalise raw country token to a clean search keyword
_COUNTRY_NORMALISE = {
    "america": "US", "american": "US", "usa": "US", "u.s.": "US", "united states": "US",
    "britain": "UK", "england": "UK", "uk": "UK",
    "european": "Europe", "eu": "Europe",
}


def _extract_world_keyword(query_text: str) -> str:
    """Return a country/region keyword from a world news query, or '' for general world news."""
    m = _WORLD_COUNTRY_RE.search(query_text)
    if not m:
        return ""
    raw = m.group(1).lower()
    return _COUNTRY_NORMALISE.get(raw, raw.title())

# Words that signal a niche/opinion/trend article — skip these in favour of
# a harder news headline. A real top headline rarely needs these qualifiers.
_LOW_QUALITY_TITLE_SIGNALS = [
    "here's why", "here is why", "this is why", "find out", "you need to know",
    "all you need", "top 5", "top 10", "in numbers", "things to know",
    "explained:", "explainer", "fact check", "opinion:", "opinion |",
    "column:", "editorial:", "interview:", "book review", "travel:",
    "recipe", "horoscope", "astrology", "zodiac",
]

# Countries/cities that are clearly NOT India — when no keyword is specified
# (general "any news?" request), skip articles whose title or first 100 chars
# of description mention these terms exclusively. Indian RSS feeds still
# occasionally surface Gulf, Pakistan, or US stories that are irrelevant
# for an India-based senior.
_NON_INDIA_GEO_SIGNALS = [
    "dubai", "abu dhabi", "uae", "saudi arabia", "qatar",
    "pakistan", "lahore", "islamabad", "karachi",
    "china", "beijing", "xi jinping",
    "ukraine", "russia", "moscow", "zelensky", "putin",
    "israel", "gaza", "hamas",
    "white house", "congress ", "pentagon", "washington dc",
]

# Topics irrelevant to Indian seniors aged 65+ — skip when no keyword specified.
# These are niche internet/youth culture topics that Indian news sites still cover.
_IRRELEVANT_TOPIC_SIGNALS = [
    # Gaming / streaming / influencer
    "streamer", "streaming platform", "gamer", "gaming tournament", "esports", "e-sports",
    "youtuber", "influencer", "content creator", "viral video", "tiktok", "instagram reel",
    "twitch", "discord server", "minecraft", "fortnite", "pubg", "bgmi",
    # Online personalities / social media drama — not news for 65+ seniors
    "online personality", "social media star", "youtube star", "instagram star",
    "allegations against", "sexual harassment allegations", "misconduct allegations",
    "cancelled", "cancel culture", "controversy over",
    # K-pop / western pop celebrity gossip
    "k-pop", "kpop", "bts ", "blackpink", "taylor swift", "kardashian",
    # Crypto / NFT
    "cryptocurrency", "bitcoin price", "nft ", "web3", "crypto market",
    # Dating apps / lifestyle trends irrelevant to this demographic
    "dating app", "tinder", "bumble", "hookup", "ghosting",
    # Crime, violence, accidents — distressing for seniors, not appropriate for a companion bot
    "kidnap", "ransom", "murder", "killed", "shot dead", "stabbed", "rape", "sexual assault",
    "blast", "explosion", "bomb", "terror attack", "shoot-out", "encounter",
    "accident", "fatal", "road crash", "pile-up", "derail", "plane crash",
    "riot", "mob", "lynching", "arson",
    # Body discovery / horror headlines
    "dead body", "body found", "found dead", "found hanging", "decomposed", "decomposing",
    "horror:", "bathroom horror", "gruesome", "missing woman", "missing girl",
    "corpse", "human remains", "skeletal remains",
    # Bug D expansion (30 Apr 2026): missing-person + body-disposal stories
    # were leaking through. The originals above only matched specific
    # demographic forms ('missing woman'/'missing girl'); the world feed
    # surfaced 'British influencer missing after Morocco trip, phone
    # switched off for days' on April 30 because no signal matched.
    "missing after", "missing for days", "missing for weeks",
    "phone switched off", "phone went off", "phone has been off",
    "abducted", "feared abducted", "feared dead", "presumed dead",
    "buried alive", "decapitated", "dismembered", "incinerator",
    "dumped body", "dumped her body", "dumped his body",
    "body inside", "body in suitcase", "body in fridge", "body in freezer",
    "body in barrel", "body in trunk",
    "killed wife", "killed husband", "killed mother", "killed father",
    "killed son", "killed daughter", "killed parents", "killed children",
    "stabbed to death", "beaten to death", "burned to death",
    # Celebrity gossip irrelevant to seniors 65+ — applies to both India and world feeds
    "rehab", "divorc", "dating again", "break up", "breakup", "affair", "cheating",
    "baby shower", "baby bump", "pregnancy reveal", "engaged to", "wedding photos",
    "wardrobe malfunction", "drunk", "dui", "arrested for",
]


def _is_india_relevant(title: str, desc: str) -> bool:
    """
    Quick geo-relevance check for when no keyword filter is active.
    Returns False if the article is clearly about a non-India topic.
    Returns True if it mentions India, or if it doesn't mention any
    known non-India geo signal (neutral = probably India by default
    since we use India-focused RSS feeds).
    """
    combined = (title + " " + desc[:150]).lower()
    # Explicitly India = always relevant
    if "india" in combined or "indian" in combined:
        return True
    # Explicitly non-India = skip
    if any(sig in combined for sig in _NON_INDIA_GEO_SIGNALS):
        return False
    # No geo signal either way — trust the India-focused feed
    return True

_NEWSAPI_URL = "https://newsapi.org/v2/top-headlines"

# Topics that map well to NewsAPI categories (kept for fallback)
_NEWSAPI_CATEGORY_MAP = {
    "business":      "business",
    "sports":        "sports",
    "health":        "health",
    "science":       "science",
    "technology":    "technology",
    "entertainment": "entertainment",
    "tech":          "technology",
    "sport":         "sports",
    "cricket":       "sports",
    "bollywood":     "entertainment",
    "films":         "entertainment",
    "movies":        "entertainment",
}


def _fetch_news_from_rss(keyword: str = "", max_results: int = 1, feeds=None) -> Optional[str]:
    """
    Try each RSS feed in order. Return up to max_results usable headlines,
    joined by newlines. Optionally filters by keyword if provided.
    Returns None if all feeds fail or return no usable headlines.

    feeds: list of RSS URLs to try. Defaults to _RSS_FEEDS_INDIA.
           Pass _RSS_FEEDS_WORLD for international queries.
    """
    import xml.etree.ElementTree as ET

    if feeds is None:
        feeds = _RSS_FEEDS_INDIA

    kw_lower = keyword.lower() if keyword else ""

    for feed_url in feeds:
        try:
            resp = requests.get(
                feed_url,
                timeout=4,
                headers={"User-Agent": "Saathi-News-Bot/1.0"},
            )
            if not resp.ok:
                logger.debug("APIS | RSS feed failed | url=%s | status=%d", feed_url, resp.status_code)
                continue

            root = ET.fromstring(resp.content)
            items = root.findall(".//item")

            # Two passes: first collect keyword-matched items, then quality-filter,
            # then fall back to any clean item if nothing passes the filter.
            candidates = []
            for item in items[:15]:  # Check first 15 items
                title_el = item.find("title")
                desc_el   = item.find("description")

                title = (title_el.text or "").strip() if title_el is not None else ""
                desc  = (desc_el.text  or "").strip() if desc_el  is not None else ""

                # Strip CDATA wrappers if present
                title = title.replace("<![CDATA[", "").replace("]]>", "").strip()
                desc  = desc.replace("<![CDATA[", "").replace("]]>", "").strip()
                # Strip any HTML tags from description
                desc = re.sub(r"<[^>]+>", " ", desc).strip()
                # Collapse whitespace
                desc = re.sub(r"\s+", " ", desc).strip()

                if not title or "[Removed]" in title or len(title) < 20:
                    continue

                # If keyword filter set, check if title or desc contains it
                if kw_lower and kw_lower not in title.lower() and kw_lower not in desc.lower():
                    continue

                # Quality check — skip niche/clickbait titles
                title_lc = title.lower()
                is_low_quality = any(sig in title_lc for sig in _LOW_QUALITY_TITLE_SIGNALS)

                # Geo-relevance check — when no keyword specified, skip articles
                # about clearly non-India topics (Dubai, Pakistan, Ukraine, etc.)
                if not kw_lower and not _is_india_relevant(title, desc):
                    logger.debug("APIS | news geo-skip | title=%s", title[:60])
                    continue

                # Topic-relevance check — skip gaming/streamer/influencer/crypto
                # content that is irrelevant to seniors aged 65+
                if not kw_lower and any(sig in title_lc for sig in _IRRELEVANT_TOPIC_SIGNALS):
                    logger.debug("APIS | news topic-skip | title=%s", title[:60])
                    continue

                # Build result string
                result = title
                if desc:
                    first_sentence = desc.split(".")[0].strip()
                    if first_sentence and first_sentence.lower() != title.lower() and len(first_sentence) > 10:
                        result = f"{title}. {first_sentence}."

                candidates.append((is_low_quality, result))

            # Collect up to max_results headlines: high-quality first, then fallback
            collected = []
            for low_q, result in candidates:
                if not low_q:
                    collected.append(result)
                if len(collected) >= max_results:
                    break
            # If not enough high-quality, pad with any candidates
            if len(collected) < max_results:
                for low_q, result in candidates:
                    if result not in collected:
                        collected.append(result)
                    if len(collected) >= max_results:
                        break

            if collected:
                return "\n".join(collected)

            logger.debug("APIS | RSS feed empty/filtered | url=%s | keyword=%s", feed_url, keyword)

        except Exception as rss_err:
            logger.debug("APIS | RSS feed error | url=%s | %s", feed_url, rss_err)
            continue

    return None


_WORLD_QUERY_RE = re.compile(
    r"\b(world|international|global|abroad|overseas|around the world|"
    r"what.{0,10}world|everywhere|"
    r"usa|america|american|u\.s\.|united states|"
    r"uk|britain|england|europe|european|"
    r"china|russia|ukraine|france|germany|japan|korea|"
    r"israel|middle east|africa|australia|canada|"
    r"iran|turkey|brazil|mexico)\b",
    re.IGNORECASE,
)


def fetch_news(interests: str = "", query_text: str = "") -> Optional[str]:
    """
    Fetch up to 3 top news headlines.

    If query_text indicates world/international intent (e.g. "what's in the USA?",
    "world news"), uses _RSS_FEEDS_WORLD (BBC, Reuters World).
    Otherwise uses _RSS_FEEDS_INDIA.

    Falls back to NewsAPI if RSS fails.

    Returns a newline-separated string of headlines, or None on failure.
    """
    # Determine feed source from message intent, not profile interests
    is_world = bool(_WORLD_QUERY_RE.search(query_text)) if query_text else False

    if is_world:
        feeds = _RSS_FEEDS_WORLD
        keyword = _extract_world_keyword(query_text)   # '' = general world, 'US' = USA
        cache_key = f"news_world:{keyword.lower() if keyword else 'world'}"
        logger.debug("APIS | news | world query detected | keyword=%s", keyword or "(general)")
    else:
        feeds = _RSS_FEEDS_INDIA
        # Bug C2 (30 Apr 2026): user `news_interests` like "family"/"cooking"
        # was extracted as the FIRST keyword and used as a HARD filter on the
        # India RSS feeds (`if kw_lower not in title and not in desc: continue`).
        # Most India top stories don't mention these lifestyle words → RSS
        # returned nothing → NewsAPI /v2/everything?q=family fallback
        # surfaced global tech press (VyOS, etc.) to seniors asking for
        # India top news. The fix: top news must not be filtered by lifestyle
        # interest. fetch_cricket_news is the dedicated path for sport-specific
        # queries; other interests can be added as ranking biases post-pilot.
        keyword = ""
        cache_key = "news:india"

    hit, cached = _cache_get(cache_key)
    if hit:
        logger.debug("APIS | news cache hit | key=%s", cache_key)
        return cached

    # ── Strategy 1: RSS feeds (primary, no key required) ──────────────────
    try:
        headline = _fetch_news_from_rss(keyword, max_results=3, feeds=feeds)
        if headline:
            _cache_set(cache_key, headline)
            logger.info(
                "APIS | news fetched via RSS | world=%s | keyword=%s | %s",
                is_world, keyword or "(none)", headline[:80],
            )
            return headline
        logger.info("APIS | news RSS returned nothing | key=%s — trying NewsAPI", cache_key)
    except Exception as rss_err:
        logger.warning("APIS | news RSS failed entirely | %s", rss_err)

    # ── Strategy 2: NewsAPI fallback ───────────────────────────────────────
    api_key = os.environ.get("NEWS_API_KEY")
    if not api_key:
        _cache_set(cache_key, None)
        return None

    try:
        params: dict = {
            "country": "in",
            "apiKey": api_key,
            "pageSize": 5,
        }
        if keyword:
            category = _NEWSAPI_CATEGORY_MAP.get(keyword.lower())
            if category:
                params["category"] = category
            else:
                params["q"] = keyword

        resp = requests.get(_NEWSAPI_URL, params=params, timeout=4)

        articles = []
        if resp.ok:
            articles = resp.json().get("articles", [])
        else:
            logger.warning(
                "APIS | NewsAPI error | status=%d | body=%s",
                resp.status_code, resp.text[:200],
            )

        if not articles:
            fallback_params = {
                "q": keyword if keyword else "India",
                "language": "en",
                "sortBy": "publishedAt",
                "apiKey": api_key,
                "pageSize": 5,
            }
            resp2 = requests.get(
                "https://newsapi.org/v2/everything",
                params=fallback_params,
                timeout=4,
            )
            if resp2.ok:
                articles = resp2.json().get("articles", [])
            else:
                logger.warning(
                    "APIS | NewsAPI everything fallback error | status=%d | body=%s",
                    resp2.status_code, resp2.text[:200],
                )

        headline = _pick_best_headline(articles)
        _cache_set(cache_key, headline)
        if headline:
            logger.info("APIS | news fetched via NewsAPI | keyword=%s | %s", keyword, headline[:80])
        return headline

    except Exception as e:
        logger.warning("APIS | NewsAPI fallback failed | %s", e)
        _cache_set(cache_key, None)
        return None


def _extract_first_keyword(interests: str) -> str:
    """
    Pull the first meaningful word from a user's interests string.
    Interests are stored as comma-separated or space-separated text from onboarding.
    Examples: "cricket, politics, health" → "cricket"
              "Bollywood films"           → "bollywood"
    """
    if not interests:
        return ""
    # Split on commas or semicolons first, then whitespace
    parts = interests.replace(";", ",").split(",")
    first = parts[0].strip().split()[0] if parts else ""
    return first.lower()


def _pick_best_headline(articles: list) -> Optional[str]:
    """
    From a list of NewsAPI articles, return the most substantive headline.
    Skips articles with '[Removed]' titles (NewsAPI placeholder for pulled articles).
    Returns title + first sentence of description if available.
    """
    for article in articles:
        title = (article.get("title") or "").strip()
        if not title or "[Removed]" in title or title == "":
            continue

        description = (article.get("description") or "").strip()
        # Take just the first sentence of the description to keep it brief
        if description:
            first_sentence = description.split(".")[0].strip()
            if first_sentence and first_sentence.lower() != title.lower():
                return f"{title}. {first_sentence}."

        return title

    return None


def fetch_cricket_news(query_text: str = "", max_results: int = 2) -> Optional[str]:
    """
    Fetch cricket news / analysis headlines from the cricket-specific RSS feeds.

    Returns up to max_results headlines joined by newlines, or None if all
    feeds fail. This is the analysis/news counterpart to fetch_cricket(),
    which provides structured live/scheduled match data from CricAPI.

    Pairing in main.py:
      - "aaj cricket?" / "score?" / "kya ho raha hai" → fetch_cricket() (live data)
      - "any cricket news?" / "Hardik kaisa khel raha?" → fetch_cricket_news() (this)

    Bug E (29 Apr) added this so cricket queries can pull genuine cricket
    coverage (ESPNCricinfo, Cricbuzz) instead of bleeding into general TOI
    sports gossip (which surfaced Lewandowski/Juventus to a senior asking
    for Indian news).
    """
    cache_key = f"cricket_news:{(query_text or '').lower()[:40] or 'general'}"
    hit, cached = _cache_get(cache_key)
    if hit:
        return cached

    # Try to extract a player or team keyword for filtering. Keep it loose —
    # a single match means we narrow the headlines; no match means general.
    keyword = ""
    if query_text:
        ql = query_text.lower()
        for kw in sorted(_TRACKED_TEAM_KEYWORDS, key=len, reverse=True):
            if len(kw) >= 3 and kw in ql:
                keyword = kw
                break

    headline = _fetch_news_from_rss(
        keyword=keyword,
        max_results=max_results,
        feeds=_RSS_FEEDS_CRICKET,
    )
    _cache_set(cache_key, headline)

    if headline:
        logger.info(
            "APIS | cricket_news fetched via RSS | keyword=%s | %s",
            keyword or "(none)", headline[:80],
        )
    else:
        logger.info("APIS | cricket_news RSS returned nothing")

    return headline
