"""Alert narrative generation and a validator that rejects unsupported content.

The model writes two to three sentences using only the stored evidence fields. The
validator rejects any narrative that contains a name, number, or capitalized term that does
not appear in the evidence, and falls back to a deterministic template.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import BaseModel, Field

from atlas.nlp.llm_client import LlmClient, LlmUnavailable

_NUMBER = re.compile(r"\d[\d.,]*")
_CAPITALIZED = re.compile(r"\b[A-ZÀ-Ý][\wÀ-ÿ'-]+")
_STOP = {"The", "A", "An", "This", "It", "In", "On", "At", "According", "Reported", "Report"}


@dataclass(frozen=True)
class Evidence:
    subject: str
    match_type: str  # for example "sanctions list entry" or "adverse media mention"
    category: str
    severity: int
    sentence: str
    source: str  # outlet or list name
    link_confidence: float


class Narrative(BaseModel):
    text: str = Field(min_length=20, max_length=600)


SYSTEM = (
    "Write a neutral two to three sentence summary of a screening alert for a human reviewer. "
    "Use only the facts in the evidence block. Do not add names, numbers, dates, or claims "
    "that are not in it. Do not state that anyone is guilty; describe reports and matches as "
    "leads for review."
)


def evidence_block(e: Evidence) -> str:
    return (
        f"subject: {e.subject}\nmatch type: {e.match_type}\ncategory: {e.category}\n"
        f"severity (1 to 4): {e.severity}\nsource: {e.source}\n"
        f"link confidence: {e.link_confidence:.2f}\nevidence sentence: {e.sentence}"
    )


def allowed_terms(e: Evidence) -> tuple[set[str], set[str]]:
    blob = evidence_block(e)
    numbers = {n.rstrip(".,") for n in _NUMBER.findall(blob)}
    words = {w.casefold() for w in _CAPITALIZED.findall(blob)}
    return numbers, words


def validate(text: str, e: Evidence) -> list[str]:
    """Return the list of violations (empty when the narrative is supported by the evidence)."""
    numbers, words = allowed_terms(e)
    problems = [
        f"number not in evidence: {n}"
        for n in {n.rstrip(".,") for n in _NUMBER.findall(text)} - numbers
    ]
    for word in _CAPITALIZED.findall(text):
        if word in _STOP:
            continue
        if word.casefold() not in words and word.casefold() not in {
            "lead",
            "leads",
            "review",
            "match",
            "human",
        }:
            problems.append(f"term not in evidence: {word}")
    return problems


def template_narrative(e: Evidence) -> str:
    return (
        f"A {e.match_type} for {e.subject} was found in {e.source}. The sentence was "
        f"categorized as {e.category.replace('_', ' ')} with severity {e.severity} of 4 and a "
        f"link confidence of {e.link_confidence:.2f}. This is a lead for human review, not a "
        "determination."
    )


def write_narrative(client: LlmClient, e: Evidence) -> tuple[str, str]:
    """Return (text, origin) where origin is ``model`` or ``template``."""
    try:
        result = client.complete(SYSTEM, evidence_block(e), Narrative)
    except LlmUnavailable:
        return template_narrative(e), "template"
    if validate(result.text, e):
        return template_narrative(e), "template"
    return result.text, "model"
