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

    # City alias map — OWM sometimes fails on common short names.
    # Try the alias if the primary query returns 404.
    _CITY_ALIASES = {
        "delhi":     "New Delhi,IN",
        "mumbai":    "Mumbai,IN",
        "bangalore": "Bengaluru,IN",
        "bengaluru": "Bengaluru,IN",
        "calcutta":  "Kolkata,IN",
        "bombay":    "Mumbai,IN",
        "madras":    "Chennai,IN",
        "pune":      "Pune,IN",
        "hyderabad": "Hyderabad,IN",
    }

    def _owm_get(q: str):
        return requests.get(
            _OWM_URL,
            params={"q": q, "appid": api_key, "units": "metric", "lang": "en"},
            timeout=8,
        )

    try:
        resp = _owm_get(city)

        # If OWM can't find the city (404), try with the country code suffix
        # or a known alias (e.g. "Delhi" → "New Delhi,IN").
        if resp.status_code == 404:
            alias = _CITY_ALIASES.get(city.lower())
            retry_q = alias if alias else f"{city},IN"
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

# Teams to identify as India (covers both senior men's and women's)
_INDIA_TEAM_KEYWORDS = {"india", "ind"}


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

    try:
        resp = requests.get(
            _CRICAPI_URL,
            params={"apikey": api_key, "offset": 0},
            timeout=8,
        )
        if not resp.ok:
            logger.warning(
                "APIS | cricket API error | status=%d | %s",
                resp.status_code, resp.text[:100],
            )
            _cache_set(cache_key, None)
            return None

        data = resp.json()
        if data.get("status") != "success":
            _cache_set(cache_key, None)
            return None

        matches = data.get("data", [])
        india_match = _find_india_match(matches)

        _cache_set(cache_key, india_match)
        if india_match:
            logger.info("APIS | cricket fetched | %s", india_match)
        else:
            logger.info("APIS | cricket fetched | no India match today")
        return india_match

    except Exception as e:
        logger.warning("APIS | cricket fetch failed | %s", e)
        _cache_set(cache_key, None)
        return None


def _find_india_match(matches: list) -> Optional[str]:
    """
    Scan the matches list for one involving India.

    Filters strictly by today's IST date so yesterday's results are never
    presented as today's news. Returns a summary prefixed with match state:

        LIVE NOW — <details>           match in progress today
        TODAY (upcoming) — <details>   scheduled today, not yet started
        COMPLETED TODAY — <details>    finished today
        UPCOMING — <details>           next India match (future date), only
                                        if nothing is happening today

    Returns None if no India match found at all.
    """
    from datetime import datetime, timezone, timedelta
    IST = timezone(timedelta(hours=5, minutes=30))
    today_ist = datetime.now(IST).strftime("%Y-%m-%d")

    today_matches = []     # (match, started, ended) for matches dated today
    upcoming_matches = []  # matches with a future date

    for match in matches:
        name = (match.get("name") or "").lower()
        teams = match.get("teams", [])
        team_names = " ".join(t.lower() for t in teams)

        is_india_match = (
            any(kw in name for kw in _INDIA_TEAM_KEYWORDS)
            or any(kw in team_names for kw in _INDIA_TEAM_KEYWORDS)
        )
        if not is_india_match:
            continue

        # CricAPI returns date as "YYYY-MM-DD" (or datetime string — take first 10 chars)
        match_date = (match.get("date") or match.get("dateTimeGMT") or "")[:10]
        match_started = bool(match.get("matchStarted", False))
        match_ended   = bool(match.get("matchEnded", False))

        if match_date == today_ist:
            today_matches.append((match, match_started, match_ended))
        elif match_date > today_ist:
            upcoming_matches.append(match)
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

    # Nothing today — surface the next scheduled India match if available
    if upcoming_matches:
        return f"UPCOMING — {_format_match_summary(upcoming_matches[0])}"

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
    # TOI Top Stories first — broadest top-of-the-hour coverage
    "https://timesofindia.indiatimes.com/rssfeeds/296589292.cms",
    # The Hindu national — reliable but sometimes niche
    "https://www.thehindu.com/news/national/feeder/default.rss",
    # NDTV India — fallback
    "https://feeds.feedburner.com/ndtvnews-india-news",
]

# Words that signal a niche/opinion/trend article — skip these in favour of
# a harder news headline. A real top headline rarely needs these qualifiers.
_LOW_QUALITY_TITLE_SIGNALS = [
    "here's why", "here is why", "this is why", "find out", "you need to know",
    "all you need", "top 5", "top 10", "in numbers", "things to know",
    "explained:", "explainer", "fact check", "opinion:", "opinion |",
    "column:", "editorial:", "interview:", "book review", "travel:",
    "recipe", "horoscope", "astrology", "zodiac",
]

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


def _fetch_news_from_rss(keyword: str = "") -> Optional[str]:
    """
    Try each RSS feed in order. Return the first usable headline found.
    Optionally filters by keyword if provided.
    Returns None if all feeds fail or return no usable headlines.
    """
    import xml.etree.ElementTree as ET

    kw_lower = keyword.lower() if keyword else ""

    for feed_url in _RSS_FEEDS_INDIA:
        try:
            resp = requests.get(
                feed_url,
                timeout=8,
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

                # Build result string
                result = title
                if desc:
                    first_sentence = desc.split(".")[0].strip()
                    if first_sentence and first_sentence.lower() != title.lower() and len(first_sentence) > 10:
                        result = f"{title}. {first_sentence}."

                candidates.append((is_low_quality, result))

            # Return first high-quality candidate; fall back to any candidate
            for low_q, result in candidates:
                if not low_q:
                    return result
            if candidates:
                return candidates[0][1]  # best available even if low-quality

            logger.debug("APIS | RSS feed empty/filtered | url=%s | keyword=%s", feed_url, keyword)

        except Exception as rss_err:
            logger.debug("APIS | RSS feed error | url=%s | %s", feed_url, rss_err)
            continue

    return None


def fetch_news(interests: str = "") -> Optional[str]:
    """
    Fetch a single top news headline for India.

    Strategy:
    1. Try public RSS feeds (no key needed, reliable).
    2. Fall back to NewsAPI if RSS fails.

    Returns a plain-text headline + brief description, or None on failure.
    """
    keyword = _extract_first_keyword(interests)
    cache_key = f"news:{keyword.lower() if keyword else 'india'}"

    hit, cached = _cache_get(cache_key)
    if hit:
        logger.debug("APIS | news cache hit | keyword=%s", keyword)
        return cached

    # ── Strategy 1: RSS feeds (primary, no key required) ──────────────────
    try:
        headline = _fetch_news_from_rss(keyword)
        if headline:
            _cache_set(cache_key, headline)
            logger.info("APIS | news fetched via RSS | keyword=%s | %s", keyword, headline[:80])
            return headline
        logger.info("APIS | news RSS returned nothing | keyword=%s — trying NewsAPI", keyword)
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

        resp = requests.get(_NEWSAPI_URL, params=params, timeout=8)

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
                timeout=8,
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
