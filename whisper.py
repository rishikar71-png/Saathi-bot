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
# Providing a hint improves accuracy; falls back to auto-detect for anything unknown.
_LANGUAGE_MAP = {
    "hindi":     "hi",
    "hinglish":  "hi",   # Hindi is the closest supported code for Hinglish
    "english":   "en",
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
