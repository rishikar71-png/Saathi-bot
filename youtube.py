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
    if _VAGUE_MESSAGES.match(text.strip()):
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

    Args:
        query: Search string.

    Returns:
        (video_title, youtube_url)

    Raises:
        ValueError if no results found.
        requests.RequestException on network failure.
    """
    api_key = os.environ["GOOGLE_CLOUD_API_KEY"]

    params = {
        "part":             "snippet",
        "q":                query,
        "type":             "video",
        "maxResults":       1,
        "regionCode":       "IN",
        "relevanceLanguage":"hi",
        "key":              api_key,
    }

    response = requests.get(_SEARCH_URL, params=params, timeout=10)
    response.raise_for_status()

    items = response.json().get("items", [])
    if not items:
        raise ValueError(f"No YouTube results for: {query!r}")

    video_id = items[0]["id"]["videoId"]
    title    = items[0]["snippet"]["title"]
    url      = f"https://www.youtube.com/watch?v={video_id}"

    logger.info("YOUTUBE | query=%r | title=%r | url=%s", query, title, url)
    return title, url


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
    return (
        f"{preamble}\n\n"
        f"*{title}*\n"
        f"{url}"
    )
