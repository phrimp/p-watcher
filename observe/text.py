"""Vietnamese text helpers for keyword matching."""

from __future__ import annotations

import unicodedata


def strip_accents(text: str) -> str:
    text = text.replace("đ", "d").replace("Đ", "D")
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def fold(text: str) -> str:
    """Lowercase, accent-free, single-spaced: for matching."""
    return " ".join(strip_accents(text).lower().split())
