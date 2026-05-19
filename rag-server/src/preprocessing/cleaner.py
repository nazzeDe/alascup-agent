from __future__ import annotations

import re


def clean_text(text: str) -> str:
    """Normalise text for embedding: strip, collapse whitespace, remove null bytes."""
    text = text.replace("\x00", "")
    text = re.sub(r"\n{2,}", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()
