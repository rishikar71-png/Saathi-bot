"""
Module 9 — Voice Output (Google Cloud Text-to-Speech)

Public interface:
    text_to_speech(text, user_language) -> bytes

Returns OGG_OPUS audio bytes ready to send as a Telegram voice message.
Uses the Google Cloud TTS REST API with GOOGLE_CLOUD_API_KEY — no service
account needed.

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
# WaveNet voices are used where available; Standard for the rest.
# All voices tested to be available as of build date.
# ---------------------------------------------------------------------------
_VOICE_MAP: dict[str, tuple[str, str]] = {
    "hindi":     ("hi-IN", "hi-IN-Wavenet-A"),   # female, warm
    "hinglish":  ("hi-IN", "hi-IN-Wavenet-A"),
    "english":   ("en-IN", "en-IN-Wavenet-D"),   # male, natural
    "tamil":     ("ta-IN", "ta-IN-Wavenet-A"),
    "bengali":   ("bn-IN", "bn-IN-Wavenet-A"),
    "marathi":   ("mr-IN", "mr-IN-Wavenet-A"),
    "gujarati":  ("gu-IN", "gu-IN-Wavenet-A"),
    "kannada":   ("kn-IN", "kn-IN-Wavenet-A"),
    "malayalam": ("ml-IN", "ml-IN-Wavenet-A"),
    # Telugu and Punjabi WaveNet not available — fall through to default
}

_DEFAULT_VOICE = ("en-IN", "en-IN-Wavenet-D")

# Markdown patterns to strip before TTS so symbols aren't read aloud
_MARKDOWN_RE = re.compile(r"[*_`#\[\]()~>|]")


def _clean_for_tts(text: str) -> str:
    """Remove markdown formatting and trim to TTS-safe length."""
    cleaned = _MARKDOWN_RE.sub("", text).strip()
    # Google TTS limit is 5000 bytes; cap at 1500 chars to keep audio concise
    return cleaned[:1500]


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
    clean_text = _clean_for_tts(text)

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
            "speakingRate": 0.9,
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
