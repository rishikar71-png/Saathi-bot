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

    try:
        resp = requests.get(
            _OWM_URL,
            params={
                "q": city,
                "appid": api_key,
                "units": "metric",
                "lang": "en",
            },
            timeout=8,
        )
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
    Returns a human-readable summary string, or None if no India match found.
    """
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

        # Build a readable summary
        match_name = match.get("name", "India match")
        match_type = match.get("matchType", "").upper()
        status = match.get("status", "")
        venue = match.get("venue", "")

        # Score — CricAPI returns scores as a list of dicts
        score_parts = []
        for score_obj in match.get("score", []):
            inning = score_obj.get("inning", "")
            runs = score_obj.get("r", "")
            wickets = score_obj.get("w", "")
            overs = score_obj.get("o", "")
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

    return None


# ---------------------------------------------------------------------------
# News — NewsAPI top headlines (India)
# Docs: https://newsapi.org/docs/endpoints/top-headlines
# Free tier: 100 calls/day — sufficient for the 20-user pilot.
# Personalised by user's news_interests field; falls back to top India news.
# ---------------------------------------------------------------------------

_NEWSAPI_URL = "https://newsapi.org/v2/top-headlines"

# Topics that map well to NewsAPI categories
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


def fetch_news(interests: str = "") -> Optional[str]:
    """
    Fetch a single top news headline from NewsAPI.

    If the user has interests set (from onboarding), uses the first keyword
    as a query filter. Falls back to general top headlines for India.

    Returns a plain-text headline + brief description suitable for passing
    to wrap_news(). Example:
        "Budget 2025: Finance Minister announces tax relief for middle class"

    Returns None if the API key is missing or the call fails.
    """
    api_key = os.environ.get("NEWS_API_KEY")
    if not api_key:
        return None

    # Extract first meaningful keyword from user's interests
    keyword = _extract_first_keyword(interests)
    cache_key = f"news:{keyword.lower() if keyword else 'india'}"

    hit, cached = _cache_get(cache_key)
    if hit:
        logger.debug("APIS | news cache hit | keyword=%s", keyword)
        return cached

    try:
        # Strategy 1: country=in with optional category/keyword filter.
        # This is the cleanest approach but the NewsAPI free tier can return
        # 0 articles for country=in unpredictably. We check and fall through.
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

        # Strategy 2: if country filter returned nothing, fall back to
        # a keyword query for India news. Free-tier country filter is
        # unreliable; the keyword query is consistently populated.
        if not articles:
            fallback_q = keyword if keyword else "India"
            fallback_params = {
                "q": fallback_q,
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
                logger.info(
                    "APIS | news | country filter empty, used everything fallback | q=%s",
                    fallback_q,
                )

        if not resp.ok and (not articles):
            logger.warning(
                "APIS | news API error | status=%d | %s",
                resp.status_code, resp.text[:100],
            )
            _cache_set(cache_key, None)
            return None

        headline = _pick_best_headline(articles)
        _cache_set(cache_key, headline)
        if headline:
            logger.info("APIS | news fetched | keyword=%s | %s", keyword, headline[:80])
        return headline

    except Exception as e:
        logger.warning("APIS | news fetch failed | %s", e)
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
