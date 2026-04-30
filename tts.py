"""
Module 9 — Voice Output (Google Cloud Text-to-Speech)
Module 17 update — WaveNet → Neural2 for Hindi and English (India).

Public interface:
    text_to_speech(text, user_language) -> bytes

Returns OGG_OPUS audio bytes ready to send as a Telegram voice message.
Uses the Google Cloud TTS REST API with GOOGLE_CLOUD_API_KEY — no service
account needed. Same endpoint and API key — Neural2 is a drop-in upgrade.

Speaking rate is set to 0.9 (slightly slower than normal) for clarity with
elderly users.

If the API call fails for any reason, the exception propagates — caller is
responsible for catching and falling back to text-only delivery.
"""

import base64
import logging
import os
import re

import requests

logger = logging.getLogger(__name__)

_TTS_URL = "https://texttospeech.googleapis.com/v1/text:synthesize"

# ---------------------------------------------------------------------------
# Voice map — (languageCode, voiceName) per Saathi language code.
#
# Module 17 upgrade:
#   Hindi and English (India) now use Neural2 — noticeably more natural,
#   better prosody, closer to a real human voice than WaveNet.
#   Same API endpoint, same key, no pricing change for these tiers.
#
#   Neural2 is only available for hi-IN and en-IN among Indian languages
#   as of this build. All other regional languages remain on WaveNet,
#   which is the best quality tier available for them.
# ---------------------------------------------------------------------------
_VOICE_MAP: dict[str, tuple[str, str]] = {
    # Neural2 — upgraded (Module 17)
    "hindi":     ("hi-IN", "hi-IN-Neural2-A"),   # female, warm — was Wavenet-A
    "hinglish":  ("hi-IN", "hi-IN-Neural2-A"),
    "english":   ("en-IN", "en-IN-Neural2-D"),   # male, natural — was Wavenet-D
    # WaveNet — best available for these languages (Neural2 not yet released)
    "tamil":     ("ta-IN", "ta-IN-Wavenet-A"),
    "bengali":   ("bn-IN", "bn-IN-Wavenet-A"),
    "marathi":   ("mr-IN", "mr-IN-Wavenet-A"),
    "gujarati":  ("gu-IN", "gu-IN-Wavenet-A"),
    "kannada":   ("kn-IN", "kn-IN-Wavenet-A"),
    "malayalam": ("ml-IN", "ml-IN-Wavenet-A"),
    # Telugu and Punjabi WaveNet not available — fall through to default
}

_DEFAULT_VOICE = ("en-IN", "en-IN-Neural2-D")

# Markdown patterns to strip before TTS so symbols aren't read aloud
_MARKDOWN_RE = re.compile(r"[*_`#\[\]()~>|]")

# Emoji and pictograph ranges to strip before TTS.
#
# Why: Google TTS Neural2 reads emoji codepoints by their Unicode names —
# 🙏 → "folded hands", ✅ → "check mark button", 👍 → "thumbs up sign".
# This makes voice notes sound broken to a senior. Strip them before any
# other speech processing.
#
# The ranges below cover ~99% of emojis seen in Saathi's reply paths:
#   U+2300–U+23FF   Miscellaneous Technical (⌚ ⏰)
#   U+2600–U+26FF   Misc Symbols (☀ ⚠)
#   U+2700–U+27BF   Dingbats (✅ ✨ ✉)
#   U+2B00–U+2BFF   Misc Symbols and Arrows (⬆ ⬇)
#   U+1F000–U+1F02F Mahjong tiles
#   U+1F0A0–U+1F0FF Playing cards
#   U+1F100–U+1F1FF Enclosed alphanumerics + regional indicators (flags)
#   U+1F300–U+1F5FF Symbols and pictographs (👍 🎵 📊 🔔)
#   U+1F600–U+1F64F Emoticons (😊 🙏)
#   U+1F680–U+1F6FF Transport & map (🚨 🚀)
#   U+1F700–U+1F77F Alchemical symbols
#   U+1F780–U+1F7FF Geometric Shapes Extended
#   U+1F800–U+1F8FF Supplemental Arrows-C
#   U+1F900–U+1F9FF Supplemental Symbols and Pictographs (🤝 🧑)
#   U+1FA00–U+1FAFF Symbols and Pictographs Extended-A
# Plus the invisible glue characters that compose emoji sequences:
#   U+200D          Zero Width Joiner
#   U+20E3          Combining Enclosing Keycap
#   U+FE00–U+FE0F   Variation Selectors (e.g. ️ FE0F after a glyph)
_EMOJI_RE = re.compile(
    "["
    "\U00002300-\U000023FF"
    "\U00002600-\U000026FF"
    "\U00002700-\U000027BF"
    "\U00002B00-\U00002BFF"
    "\U0001F000-\U0001F02F"
    "\U0001F0A0-\U0001F0FF"
    "\U0001F100-\U0001F1FF"
    "\U0001F300-\U0001F5FF"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "\U0001F700-\U0001F77F"
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FAFF"
    "\U0000200D"
    "\U000020E3"
    "\U0000FE00-\U0000FE0F"
    "]+",
    flags=re.UNICODE,
)


def _strip_emojis(text: str) -> str:
    """Remove emoji codepoints + variation selectors / ZWJ glue.

    Applied before _add_speech_pauses so speech-pause logic never sees
    emoji bytes. Trailing whitespace and double spaces left by the
    substitution are collapsed.
    """
    text = _EMOJI_RE.sub("", text)
    # Collapse double spaces left where an emoji sat between two words
    text = re.sub(r"  +", " ", text)
    # Collapse " ." or " ," left where an emoji sat right before punctuation
    text = re.sub(r"\s+([.,!?;:])", r"\1", text)
    return text.strip()


def _clean_for_tts(text: str) -> str:
    """Strip markdown + emojis and trim to TTS-safe length."""
    cleaned = _MARKDOWN_RE.sub("", text)
    cleaned = _strip_emojis(cleaned)
    # Google TTS limit is 5000 bytes; cap at 1500 chars to keep audio concise
    return cleaned[:1500]


def _add_speech_pauses(text: str) -> str:
    """
    Add natural pause cues to text before sending to Neural2.

    Neural2 uses punctuation as its primary prosody signal. Without these fixes,
    responses that DeepSeek generates — which use em-dashes, ellipses, and run-on
    clauses — arrive at TTS as a single breathless stream.

    Changes made:
    - Em-dash ( — ) → comma-space. DeepSeek uses these for emphasis and transition.
      Neural2 barely pauses at an em-dash; it pauses properly at a comma.
    - Ellipsis (...) → comma. Prevents Neural2 from hesitating oddly on three dots.
    - Hindi full-stop (।) → period-space. Neural2 does not recognise the Devanagari
      danda as a sentence boundary — without this it runs Hindi sentences together.
    - Greeting + name → comma inserted. "Namaste Ramesh" → "Namaste, Ramesh".
      The comma creates the natural beat a human speaker always puts there.
    - Multiple spaces collapsed to one (cleanup after replacements).
    """
    # Em-dash variants → comma pause
    text = text.replace(" — ", ", ")
    text = text.replace("—", ", ")

    # Ellipsis → comma (three or more dots)
    text = re.sub(r'\.{3,}', ',', text)

    # Hindi/Devanagari full stop → period so Neural2 treats it as a sentence end
    text = text.replace('।', '. ')

    # Insert comma after greeting word before a capitalised name
    # "Namaste Ramesh ji" → "Namaste, Ramesh ji"
    text = re.sub(
        r'\b(Namaste|Namaskar|Hello|Good morning|Good afternoon|Good evening|Good night)\s+([A-Z])',
        r'\1, \2',
        text,
    )

    # Collapse multiple spaces left by replacements
    text = re.sub(r'  +', ' ', text)

    return text.strip()


def text_to_speech(text: str, user_language: str = "english") -> bytes:
    """
    Convert text to speech using Google Cloud TTS.

    Args:
        text:          The text to speak. Markdown is stripped automatically.
        user_language: The user's language preference from their profile.

    Returns:
        OGG_OPUS audio bytes — pass directly to Telegram reply_voice().

    Raises:
        ValueError:  If the API returns an error response.
        requests.RequestException: On network failure.
    """
    api_key = os.environ["GOOGLE_CLOUD_API_KEY"]
    lang_code, voice_name = _VOICE_MAP.get(user_language.lower(), _DEFAULT_VOICE)
    # Strip markdown first, then add natural pause cues
    clean_text = _add_speech_pauses(_clean_for_tts(text))

    if not clean_text:
        raise ValueError("Nothing to speak after cleaning text")

    payload = {
        "input": {"text": clean_text},
        "voice": {
            "languageCode": lang_code,
            "name": voice_name,
        },
        "audioConfig": {
            "audioEncoding": "OGG_OPUS",
            "speakingRate": 0.85,   # 0.9 → 0.85: more deliberate, less digital rush
            "pitch": 0.0,
        },
    }

    response = requests.post(
        _TTS_URL,
        params={"key": api_key},
        json=payload,
        timeout=15,
    )

    if not response.ok:
        raise ValueError(
            f"Google TTS error {response.status_code}: {response.text[:200]}"
        )

    audio_b64 = response.json().get("audioContent", "")
    if not audio_b64:
        raise ValueError("Google TTS returned empty audioContent")

    audio_bytes = base64.b64decode(audio_b64)

    logger.info(
        "TTS | lang=%s | voice=%s | input_len=%d | audio_bytes=%d",
        lang_code, voice_name, len(clean_text), len(audio_bytes),
    )

    return audio_bytes
