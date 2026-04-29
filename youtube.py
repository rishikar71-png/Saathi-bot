"""
Module 10 — Music via YouTube Data API v3

Public interface:
    detect_music_request(text) -> str | None
        Returns a search query if the message is a music request, else None.

    find_music(query) -> tuple[str, str]
        Searches YouTube and returns (video_title, url).

    build_music_message(title, url) -> str
        Builds the warm reply to send to the user.

Uses GOOGLE_CLOUD_API_KEY — the same key used for TTS.
Note: ensure YouTube Data API v3 is enabled in the Google Cloud project
for this key.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re

import requests

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"

# ---------------------------------------------------------------------------
# Music request detection — English + Hindi/Hinglish patterns
# Returns the best search query extracted from the message, or None.
# ---------------------------------------------------------------------------

# Signals that a message is a music request
_MUSIC_SIGNALS = [
    # English
    r"\bplay\b",
    r"\bsong\b",
    r"\bsongs?\b",
    r"\bmusic\b",
    r"\bbhajan\b",
    r"\bbhajans?\b",
    r"\bghazal\b",
    r"\bghazals?\b",
    r"\bgazal\b",
    r"\bqawwali\b",
    r"\bclassical\b",
    r"\bdevotional\b",
    r"\bkirtan\b",
    r"\bkirtans?\b",
    r"\bbollywood\b",
    r"\bmelody\b",
    r"\btune\b",
    r"\bsing\b",
    r"\blisten\b",
    r"want to hear",
    r"want to listen",
    r"put.?on.{0,10}(song|music|gana|bhajan)",
    # Hindi / Hinglish
    r"\bgana\b",
    r"\bgaana\b",
    r"\bgaane\b",
    r"\bsunao\b",
    r"\bsuno\b",
    r"\bsangeet\b",
    r"\bbajao\b",
    r"\bsunna\b",
    r"\bsunni\b",
    r"sunna chahta",
    r"sunna chahti",
    r"koi gana",
    r"koi gaana",
    r"kuch sunao",
    r"kuch bajao",
    r"kuch gana",
    r"purana gana",
    r"purane gaane",
    r"purani gazal",
    r"filmi gana",
    r"film gana",
    r"\bbhakti\b",
    r"\baarti\b",
    r"\barti\b",
    r"\bstotra\b",
    r"\bshlok\b",
    r"\bchaupai\b",
    r"\bmantra\b",
    r"\bstuti\b",
    r"\braag\b",
    r"\braga\b",
    r"\bthumri\b",
    r"\bdadra\b",
    r"\bshabads?\b",
]

_SIGNAL_RE = [re.compile(p, re.IGNORECASE) for p in _MUSIC_SIGNALS]

# Exclusion patterns — conversational uses of "listen" / "suno" that are NOT
# music requests.  If ANY exclusion matches, detect_music_request returns None.
_EXCLUSION_PATTERNS = [
    r"they?\s+don'?t\s+listen",
    r"nobody\s+listen",
    r"no\s*one\s+listen",
    r"won'?t\s+listen",
    r"doesn'?t\s+listen",
    r"don'?t\s+listen\s+to\s+me",
    r"not\s+listen",
    r"never\s+listen",
    r"listen\s+to\s+me",       # "please listen to me" (plea, not music)
    r"koi\s+(nahi\s+)?sun(ta|ti)",   # "koi nahi sunta/sunti"
    r"sun(o|iye)\s+meri\s+baat",     # "suno meri baat" / "suniye meri baat"
    r"koi\s+nahi\s+sun",
]
_EXCLUSION_RE = [re.compile(p, re.IGNORECASE) for p in _EXCLUSION_PATTERNS]

# Command words to strip from the front when extracting the search query
_STRIP_PREFIX_RE = re.compile(
    r"^(please\s+)?"
    r"(can you\s+)?"
    r"(zara\s+)?"
    r"(mujhe\s+)?"
    r"(kuch\s+)?"
    r"(ek\s+)?"
    r"("
    r"play(\s+me|\s+something|\s+kuch|\s+koi)?"
    r"|put on"
    r"|sunao"
    r"|bajao"
    r"|suno"
    r"|i want to (hear|listen to)"
    r"|want to (hear|listen to)"
    r")\s*",
    re.IGNORECASE,
)

# Generic fallback queries when the message is too vague to search directly
_VAGUE_MESSAGES = re.compile(
    r"^(play something|kuch sunao|kuch bajao|kuch gana sunao"
    r"|koi gana|koi gaana|kuch accha sunao|sunao|gana sunao"
    r"|music (lagao|chalao)|gana baja do|something nice"
    r"|play|play music|just play something)\.?$",
    re.IGNORECASE,
)

# Generic filler words. If EVERY word in the message is in this set, the
# request carries no artist/title/genre signal and we should fall back to
# the user's stored music_preferences.  This catches phrasings the
# _VAGUE_MESSAGES regex misses — e.g. "get me a good song to listen to",
# "can you play something nice for me", "mujhe ek accha gana sunao".
_GENERIC_MUSIC_FILLERS = {
    # English
    "a", "an", "the", "any", "some", "something", "anything", "one",
    "song", "songs", "music", "tune", "tunes", "melody",
    "good", "nice", "sweet", "soft", "slow", "fast",
    "please", "kindly",
    "play", "hear", "listen", "listening", "sing", "put", "on",
    "get", "give", "find",
    "me", "for", "to", "let", "us",
    "can", "could", "would", "you", "i", "want", "wanna", "like",
    # Hindi/Hinglish
    "kuch", "koi", "ek", "accha", "acha", "achha", "achcha",
    "mujhe", "aap", "zara", "bas",
    "gana", "gaana", "gaane", "sangeet",
    "sunao", "suno", "sunna", "sunni", "bajao", "baja", "do",
    "chahta", "chahti", "chahiye", "hai", "hain", "hoon", "hu",
}


def _is_all_filler(text: str) -> bool:
    """True if every alphabetic word in the message is a generic filler."""
    words = re.findall(r"[A-Za-z\u0900-\u097F]+", text.lower())
    if not words:
        return False
    return all(w in _GENERIC_MUSIC_FILLERS for w in words)


def detect_music_request(text: str, music_preferences: str = "") -> str | None:
    """
    Decide if the message is a music request and extract a search query.

    Args:
        text:               The incoming message.
        music_preferences:  User's music prefs from onboarding (for vague requests).

    Returns:
        A search query string if this is a music request, else None.
    """
    # Must match at least one signal
    if not any(sig.search(text) for sig in _SIGNAL_RE):
        return None

    # Exclude conversational uses of "listen" / "suno" that are NOT music requests
    if any(exc.search(text) for exc in _EXCLUSION_RE):
        return None

    # Vague request — use user's preferences if available, else a warm default
    # Two ways to be vague:
    #   (a) message is in the hardcoded _VAGUE_MESSAGES list, OR
    #   (b) every word is a generic filler ("get me a good song to listen to")
    if _VAGUE_MESSAGES.match(text.strip()) or _is_all_filler(text):
        if music_preferences:
            return f"{music_preferences} Indian songs"
        return "old Hindi songs evergreen classics"

    # Strip leading command words to get the core search query
    query = _STRIP_PREFIX_RE.sub("", text.strip())

    # If stripping gutted it, fall back to full text
    if len(query) < 3:
        query = text.strip()

    # Append "Indian" context if not already present — improves YouTube results
    query_lower = query.lower()
    if not any(w in query_lower for w in ("india", "hindi", "bollywood", "bhajan",
                                          "classical", "ghazal", "gazal", "qawwali",
                                          "punjabi", "tamil", "telugu", "bengali",
                                          "marathi", "gujarati", "kannada")):
        query = query + " Indian"

    return query.strip()


# ---------------------------------------------------------------------------
# YouTube search
# ---------------------------------------------------------------------------

def find_music(query: str) -> tuple[str, str]:
    """
    Search YouTube Data API v3 for the top video matching query.

    Three-attempt fallback chain so we never show a failure message:
      1. Original query as-is
      2. Query with noise adjectives stripped ("an old", "a", "purana", etc.)
      3. Artist/key-noun only (first 2–3 meaningful words)

    Args:
        query: Search string.

    Returns:
        (video_title, youtube_url)

    Raises:
        ValueError if all three attempts return no results.
        requests.RequestException on network failure.
    """
    api_key = os.environ["GOOGLE_CLOUD_API_KEY"]

    def _search(q: str) -> tuple[str, str] | None:
        """Single search attempt. Returns (title, url) or None."""
        params = {
            "part":              "snippet",
            "q":                 q,
            "type":              "video",
            "maxResults":        1,
            "regionCode":        "IN",
            "relevanceLanguage": "hi",
            "key":               api_key,
        }
        resp = requests.get(_SEARCH_URL, params=params, timeout=10)
        resp.raise_for_status()
        items = resp.json().get("items", [])
        if not items:
            return None
        video_id = items[0]["id"]["videoId"]
        title    = items[0]["snippet"]["title"]
        url      = f"https://www.youtube.com/watch?v={video_id}"
        logger.info("YOUTUBE | query=%r | title=%r | url=%s", q, title, url)
        return title, url

    def _stripped_query(q: str) -> str:
        """Remove common noise adjectives/articles from the front of the query."""
        noise = re.compile(
            r"^(an?\s+)?(the\s+)?"
            r"(old|new|purana|purani|classic|latest|best|popular|favourite|favorite|"
            r"beautiful|sweet|soft|slow|fast|great|good|famous|hit|super|some|"
            r"ek\s+|koi\s+|kuch\s+)",
            re.IGNORECASE,
        )
        cleaned = noise.sub("", q).strip()
        return cleaned if len(cleaned) >= 3 else q

    def _short_query(q: str) -> str:
        """Take the first 2–3 meaningful words (artist + genre, drop 'song/songs/Indian')."""
        stop_words = {"song", "songs", "gana", "gaana", "gaane", "music", "indian",
                      "hindi", "bollywood", "hit", "hits", "old", "new", "purana"}
        words = [w for w in q.split() if w.lower() not in stop_words]
        return " ".join(words[:3]) if words else q

    # Attempt 1 — original query
    result = _search(query)
    if result:
        return result

    # Attempt 2 — strip noise adjectives
    query2 = _stripped_query(query)
    if query2 != query:
        logger.info("YOUTUBE | retry stripped: %r", query2)
        result = _search(query2)
        if result:
            return result

    # Attempt 3 — artist/key noun only
    query3 = _short_query(query)
    if query3 != query2:
        logger.info("YOUTUBE | retry short: %r", query3)
        result = _search(query3)
        if result:
            return result

    raise ValueError(f"No YouTube results after 3 attempts for: {query!r}")


# ---------------------------------------------------------------------------
# Response builder
# ---------------------------------------------------------------------------

def build_music_message(title: str, url: str, language: str = "english") -> str:
    """
    Build a warm, varied music response.
    Rotates across several preambles so it never feels templated.
    Uses a hash of the title to pick a variant deterministically —
    same song → same preamble (feels natural; not random on retry).
    """
    _hash = int(hashlib.md5(title.encode()).hexdigest(), 16)
    _variant = _hash % 5

    if language in ("hindi", "hinglish"):
        _preambles = [
            "Yeh lijiye 🎵",
            "Mil gaya —",
            "Yeh suniye —",
            "Aapke liye 🎶",
            "Laa diya —",
        ]
    else:
        _preambles = [
            "Here you go 🎵",
            "Found it —",
            "This one's for you 🎶",
            "Here it is —",
            "Coming right up 🎵",
        ]

    preamble = _preambles[_variant]
    # Trim long YouTube titles (compilations often have multi-part names)
    display_title = title if len(title) <= 60 else title[:57].rsplit(" ", 1)[0] + "…"
    return (
        f"{preamble}\n\n"
        f"{display_title}\n"
        f"{url}"
    )
