"""
language_utils.py — shared per-message language detection.

Pure stdlib, no project imports. Safe to import from any module without
circular-import risk. Extracted from main.py on 1 May 2026 (Bug FB-3 fix)
so family.py can match relay-wrapper language to message script instead of
reading the senior's stored language (which drifts via the implicit
script-detection learning loop).

Public API:
- detect_message_language(text) -> "hindi" | "hinglish" | "english"

Note: stateful learning lives in main.py (_update_language_learning).
This module is intentionally pure and idempotent — same input, same output.
"""

# Common Hindi/Urdu words written in Roman script. Matched word-boundary aware.
# Kept short and high-precision — false positives are fine, missed Hinglish
# falls through to "english" which is the safe default.
_HINGLISH_MARKERS = [
    "hoon", "hun", "hai", "hain", "tha", "thi", "the",
    "kya", "nahi", "nhi", "acha", "achha", "theek", "thik",
    "bilkul", "haan", "naa", "bhi", "aur", "lekin", "par",
    "mujhe", "mera", "meri", "mere", "aap", "tum", "main",
    "kuch", "bahut", "thoda", "zyada", "bohot",
    "abhi", "aaj", "kal", "phir", "dobara",
    "ghar", "khana", "pani", "beta", "beti",
    "ji", "yaar", "bhai", "didi",
]


def detect_message_language(text: str) -> str:
    """
    Detect the dominant language of a message.

    Returns: 'hindi', 'hinglish', or 'english'.

    Rules:
    - If the message contains significant Devanagari script → 'hindi'
    - If the message contains common Hindi/Urdu words in Roman script
      (at least 2 known words) → 'hinglish'
    - Otherwise → 'english'

    This is intentionally simple — false positives are fine.
    The goal is to catch clear cases, not edge cases.
    """
    if not text:
        return "english"

    # Count Devanagari characters (Unicode block U+0900–U+097F)
    devanagari_count = sum(
        1 for ch in text
        if 'ऀ' <= ch <= 'ॿ'
    )
    # If more than 3 Devanagari chars, it's Hindi
    if devanagari_count > 3:
        return "hindi"

    # Check for common Hindi/Urdu words written in Roman script.
    # Word-boundary aware: split on spaces and punctuation.
    text_lower = text.lower()
    words = set(
        text_lower
        .replace(",", " ")
        .replace(".", " ")
        .replace("?", " ")
        .replace("!", " ")
        .replace(";", " ")
        .replace(":", " ")
        .split()
    )
    hinglish_hits = sum(1 for w in _HINGLISH_MARKERS if w in words)
    if hinglish_hits >= 2:
        return "hinglish"

    return "english"
