"""Name normalization utilities shared by loaders, resolution, and screening.

Pipeline for a name: Unicode NFKC, case folding, accent and punctuation removal,
whitespace collapse, optional company suffix stripping, and token sorting.
Transliteration to ASCII uses ``anyascii`` (ISC licence) and is exposed separately
so that original-script names remain searchable.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from anyascii import anyascii

# Legal-form tokens removed from organization names (compared after case folding).
COMPANY_SUFFIXES: frozenset[str] = frozenset(
    {
        "ltd",
        "limited",
        "llc",
        "llp",
        "lp",
        "inc",
        "incorporated",
        "corp",
        "corporation",
        "co",
        "company",
        "plc",
        "gmbh",
        "ag",
        "kg",
        "ug",
        "sa",
        "sas",
        "sarl",
        "srl",
        "spa",
        "bv",
        "nv",
        "ab",
        "as",
        "oy",
        "aps",
        "pte",
        "pty",
        "bhd",
        "sdn",
        "jsc",
        "pjsc",
        "ojsc",
        "oao",
        "zao",
        "ooo",
        "ao",
        "fze",
        "fzco",
        "fzc",
        "dmcc",
        "sl",
        "slu",
        "sc",
        "ltda",
        "de",
        "cv",
        "kk",
        "tbk",
        "pt",
        "cia",
        "eurl",
        "gbr",
        "ev",
        "eg",
        "se",
        "ve",
        "ticaret",
        "anonim",
        "sirketi",
        "sanayi",
        "limitada",
        "sociedad",
        "anonima",
    }
)
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_SPACE = re.compile(r"\s+")
# Characters that casefold/NFKC leave unchanged but that should act as spaces.
_JOINERS = re.compile(r"[-_/\\.,;:()\[\]{}\"'`´’‘“”]")


@dataclass(frozen=True)
class NormalizedName:
    """Normalized variants of one name.

    full:     NFKC, case folded, accents removed, punctuation removed.
    stripped: ``full`` with company suffixes removed (same as full for persons).
    key:      ASCII transliteration of ``stripped`` with tokens sorted.
    """

    full: str
    stripped: str
    key: str


def fold(text: str) -> str:
    """NFKC, case fold, strip combining marks, replace joiners with spaces."""
    text = unicodedata.normalize("NFKC", text).casefold()
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = _JOINERS.sub(" ", text)
    text = _PUNCT.sub("", text)
    return _SPACE.sub(" ", text).strip()


def transliterate(text: str) -> str:
    """Transliterate any script to ASCII (lossy, for matching only)."""
    return _SPACE.sub(" ", anyascii(text)).strip()


def strip_company_suffixes(tokens: list[str]) -> list[str]:
    """Remove legal-form tokens, but never reduce a name to nothing."""
    kept = [token for token in tokens if token not in COMPANY_SUFFIXES]
    return kept or tokens


def sort_tokens(text: str) -> str:
    return " ".join(sorted(text.split()))


def normalize_name(name: str, kind: str = "organization") -> NormalizedName:
    """Normalize ``name``. ``kind`` is ``person`` or ``organization`` (suffixes only for orgs)."""
    full = fold(name)
    tokens = full.split()
    if kind == "organization":
        tokens = strip_company_suffixes(tokens)
    stripped = " ".join(tokens)
    key = sort_tokens(fold(transliterate(stripped)) if stripped else "")
    return NormalizedName(full=full, stripped=stripped, key=key)


def script_of(text: str) -> str:
    """Return the dominant Unicode script name (LATIN, CYRILLIC, ARABIC, ...)."""
    counts: dict[str, int] = {}
    for ch in text:
        if ch.isalpha():
            script = unicodedata.name(ch, "UNKNOWN").split(" ")[0]
            counts[script] = counts.get(script, 0) + 1
    return max(counts, key=lambda s: counts[s]) if counts else "NONE"
