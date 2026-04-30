"""
Module 8 — Voice Input (OpenAI Whisper)

Transcribes voice note bytes to text.
Called by main.py after downloading a Telegram voice file.

Uses the synchronous OpenAI client — acceptable for MVP on Railway
with a small user base. For v2, wrap in asyncio.to_thread() if needed.
"""

from __future__ import annotations

import io
import logging
import os

from openai import OpenAI

logger = logging.getLogger(__name__)

# Whisper language codes for the languages Saathi supports.
#
# Bug F fix (30 Apr 2026): hindi/hinglish/english were mapped to a hint
# code, which made Whisper TRANSLATE Hindi voice notes to English when
# the senior's stored language was 'english'. A senior set up by family
# as English-speaker but voice-noting in Hindi got silent translation —
# DeepSeek then saw English text, replied in English, TTS spoke English,
# and the senior felt unheard.
#
# Solution: leave hindi / hinglish / english UNMAPPED so Whisper
# auto-detects from the audio itself. The downstream script-detection
# in main.py + the per-turn language nudge in deepseek.py correctly
# handle whatever script Whisper returns. Whisper-1 auto-detection is
# excellent on Hindi, Hinglish, and Indian-accented English.
#
# Regional language hints kept — Whisper auto-detect is less reliable
# for ta/te/bn/mr/gu/pa/kn/ml from low-quality audio, and the senior's
# stored language is a strong prior for these.
_LANGUAGE_MAP = {
    # Common three: NO hint — let Whisper auto-detect from audio.
    # "hindi":   None,
    # "hinglish":None,
    # "english": None,
    # Regional languages: stored profile is a useful prior.
    "tamil":     "ta",
    "telugu":    "te",
    "bengali":   "bn",
    "marathi":   "mr",
    "gujarati":  "gu",
    "punjabi":   "pa",
    "kannada":   "kn",
    "malayalam": "ml",
}

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client


def transcribe_voice(file_bytes: bytes, user_language: str = "hindi") -> str:
    """
    Transcribe voice bytes using OpenAI Whisper.

    Args:
        file_bytes:    Raw bytes of the OGG/Opus voice file from Telegram.
        user_language: The user's language preference from their profile
                       (used as a hint to Whisper for better accuracy).

    Returns:
        Transcribed text string.

    Raises:
        Exception on API failure — caller is responsible for handling.
    """
    whisper_lang = _LANGUAGE_MAP.get(user_language.lower())

    audio_file = io.BytesIO(file_bytes)
    # Whisper infers format from the filename extension.
    # Telegram voice notes are OGG/Opus — Whisper handles this natively.
    audio_file.name = "voice.ogg"

    kwargs = dict(model="whisper-1", file=audio_file)
    if whisper_lang:
        kwargs["language"] = whisper_lang

    response = _get_client().audio.transcriptions.create(**kwargs)
    text = response.text.strip()

    logger.info(
        "WHISPER | lang_hint=%s | transcription_len=%d | text=%s",
        whisper_lang or "auto",
        len(text),
        text[:80],
    )

    return text
