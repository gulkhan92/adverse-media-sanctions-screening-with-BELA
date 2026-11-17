"""Sentence-aware chunking to the encoder's maximum length, with sentence overlap."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache

import pysbd

# pysbd has no Turkish rules; English rules are an acceptable fallback for sentence ends.
PYSBD_LANGS = {"en", "es", "fr", "de", "ar", "ru"}


@dataclass(frozen=True)
class Chunk:
    text: str
    start: int  # character offset of the chunk in the source text
    end: int


@lru_cache(maxsize=16)
def _segmenter(lang: str) -> pysbd.Segmenter:
    return pysbd.Segmenter(
        language=lang if lang in PYSBD_LANGS else "en", clean=False, char_span=True
    )


def sentences(text: str, lang: str) -> list[tuple[int, int]]:
    """Return (start, end) character spans of sentences."""
    spans = _segmenter(lang).segment(text)
    return [(s.start, s.end) for s in spans if text[s.start : s.end].strip()]


def approx_tokens(text: str) -> int:
    """Cheap token estimate used when no tokenizer is supplied (about 3.5 characters each)."""
    return max(1, round(len(text) / 3.5))


def chunk_text(
    text: str,
    lang: str,
    max_tokens: int = 256,
    overlap_sentences: int = 1,
    count_tokens: Callable[[str], int] = approx_tokens,
) -> list[Chunk]:
    """Split ``text`` into chunks of at most ``max_tokens`` at sentence boundaries.

    A sentence longer than the limit is split at whitespace. Consecutive chunks share
    ``overlap_sentences`` sentences so that mentions near a boundary keep their context.
    Chunk offsets refer to the original text.
    """
    spans: list[tuple[int, int]] = []
    for start, end in sentences(text, lang):
        spans.extend(_split_long(text, start, end, max_tokens, count_tokens))
    chunks: list[Chunk] = []
    index = 0
    while index < len(spans):
        first, last, total = index, index, 0
        while last < len(spans):
            size = count_tokens(text[spans[last][0] : spans[last][1]])
            if total + size > max_tokens and last > first:
                break
            total += size
            last += 1
        chunks.append(
            Chunk(text[spans[first][0] : spans[last - 1][1]], spans[first][0], spans[last - 1][1])
        )
        if last >= len(spans):
            break
        index = max(first + 1, last - overlap_sentences)
    return chunks


def _split_long(
    text: str, start: int, end: int, max_tokens: int, count_tokens: Callable[[str], int]
) -> list[tuple[int, int]]:
    if count_tokens(text[start:end]) <= max_tokens:
        return [(start, end)]
    pieces: list[tuple[int, int]] = []
    piece_start = start
    cursor = start
    while cursor < end:
        next_space = text.find(" ", cursor + 1, end)
        next_space = end if next_space == -1 else next_space
        if count_tokens(text[piece_start:next_space]) > max_tokens and cursor > piece_start:
            pieces.append((piece_start, cursor))
            piece_start = cursor + 1 if text[cursor : cursor + 1] == " " else cursor
        cursor = next_space if next_space > cursor else cursor + 1
        if next_space == end:
            break
    if piece_start < end:
        pieces.append((piece_start, end))
    return pieces
