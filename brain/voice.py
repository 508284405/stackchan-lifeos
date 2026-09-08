"""Bounded semantic voice requests for Phase 3.

This module deliberately stops at a validated request. Audio drivers and media
transports remain separately gated until hardware and bandwidth acceptance.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import re


SUPPORTED_LANGUAGES = frozenset({"zh-CN", "en-US"})
SUPPORTED_VOICES = frozenset({"default", "calm", "friendly"})
MAX_VOICE_TEXT_CHARS = 512
MAX_VOICE_TTL_SECONDS = 30.0


@dataclass(frozen=True)
class VoiceRequest:
    text: str
    language: str
    voice: str
    ttl_seconds: float


class VoiceAdapter:
    """Validate speech without buffering or invoking an audio provider."""

    def __init__(
        self,
        *,
        languages: frozenset[str] = SUPPORTED_LANGUAGES,
        voices: frozenset[str] = SUPPORTED_VOICES,
        max_text_chars: int = MAX_VOICE_TEXT_CHARS,
        max_ttl_seconds: float = MAX_VOICE_TTL_SECONDS,
    ) -> None:
        if max_text_chars <= 0 or max_ttl_seconds <= 0:
            raise ValueError("voice limits must be positive")
        if not languages or not voices:
            raise ValueError("voice allowlists must not be empty")
        self.languages = frozenset(languages)
        self.voices = frozenset(voices)
        self.max_text_chars = max_text_chars
        self.max_ttl_seconds = max_ttl_seconds

    def prepare(
        self,
        text: str,
        *,
        language: str = "zh-CN",
        voice: str = "default",
        ttl_seconds: float = 8.0,
        device_connected: bool = True,
    ) -> VoiceRequest | None:
        """Return a one-shot request; disconnected devices are never queued."""
        if not device_connected:
            return None
        if not isinstance(text, str):
            raise ValueError("voice text must be a string")
        normalized = text.strip()
        if not normalized:
            raise ValueError("voice text must not be empty")
        if len(normalized) > self.max_text_chars:
            raise ValueError("voice text exceeds size budget")
        if language not in self.languages or not re.fullmatch(r"[A-Za-z]{2,3}-[A-Za-z]{2,4}", language):
            raise ValueError("voice language is not allowed")
        if voice not in self.voices:
            raise ValueError("voice is not allowed")
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, (int, float)) or not math.isfinite(float(ttl_seconds)):
            raise ValueError("voice TTL must be finite")
        if ttl_seconds <= 0 or ttl_seconds > self.max_ttl_seconds:
            raise ValueError("voice TTL is outside the allowed range")
        return VoiceRequest(normalized, language, voice, float(ttl_seconds))
