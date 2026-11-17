"""Deterministic guards around model output for untrusted article text.

A small model follows instructions embedded in article text (measured in docs/eval). These
checks do not try to stop the model from being steered. They detect likely steering and
disagreement so that the result is flagged for human review instead of being trusted:

  injection_signals   instruction-like or role-marker text inside the evidence sentence
  lexical_adverse     adverse vocabulary in seven languages that a ``none`` label contradicts
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Phrases that address a model or an operator. Strong signals alone are enough to flag.
STRONG = [
    r"ignore (all |any )?(the )?(previous|prior|above) (instructions|rules|prompts?)",
    r"disregard (the |all |any )?(schema|instructions|rules|previous)",
    r"(new|updated) (instruction|task)s?\s*[:\-]",
    r"(system|assistant|developer|admin(istrator)?)\s*(prompt|override|message)?\s*[:\]]",
    r"\[(admin|system|override)[^\]]*\]",
    r"respond with\s*[:{]|output (only )?(the )?(category|json)|answer (is|with) ",
    r"(note|message|instruction)s? (to|for) (the )?(ai|model|classifier|assistant|reviewer)",
    r"(always|must) (answer|label|respond|output)",
    r"ignora (todas )?las instrucciones|ignorez (toutes )?les instructions",
    r"ignoriere (alle )?(vorherigen )?anweisungen",
    r"игнорируй(те)? (все )?(предыдущие )?инструкции",
    r"تجاهل (جميع )?التعليمات",
    r"önceki talimatları (yok say|görmezden gel)",
]
WEAK = [
    r"\b(category|severity|schema|classifier|label(ed|l?ing)?)\b",
    r"</?[a-z_]{3,}>|<<<|>>>|###|```",
    r"\bjson\b|\{\s*\"",
    r"\b(ai|llm|language model|chatbot)\b",
]
ADVERSE = [
    # English
    r"fraud|brib|corrupt|launder|embezzl|sanction|terroris|indict|charged with|convicted|"
    r"fined|investigat|arrest|trafficking|racketeer|cartel|illicit|penalt",
    # Spanish
    r"fraude|soborno|corrup|lavado|malversaci|sancion|terroris|acus|condena|multa|arrest|"
    r"narco|investigaci",
    # French
    r"fraude|corruption|pots-de-vin|blanchiment|détournement|sanction|terroris|inculp|condamn|"
    r"amende|arrêt|enquête",
    # German
    r"betrug|korrupt|bestech|geldwäsche|veruntreu|sanktion|terror|angeklagt|verurteilt|"
    r"bußgeld|festgenommen|ermittl",
    # Russian
    r"мошеннич|коррупц|взяточ|отмыван|растрат|санкци|террор|обвин|осужд|штраф|арест|"
    r"расследован",
    # Arabic
    r"احتيال|فساد|رشوة|غسل|اختلاس|عقوبات|إرهاب|اتهم|أدين|غرامة|اعتقل|تحقيق",
    # Turkish
    r"dolandırıcılık|yolsuzluk|rüşvet|kara para|zimmet|yaptırım|terör|suçlan|mahkum|para cezası|"
    r"gözaltı|soruşturma",
]
_STRONG = [re.compile(p, re.IGNORECASE) for p in STRONG]
_WEAK = [re.compile(p, re.IGNORECASE) for p in WEAK]
_ADVERSE = re.compile("|".join(ADVERSE), re.IGNORECASE)


@dataclass(frozen=True)
class GuardResult:
    injection_suspected: bool
    lexical_adverse: bool
    contradicts_none: bool

    @property
    def needs_review(self) -> bool:
        return self.injection_suspected or self.contradicts_none


def injection_signals(text: str) -> int:
    """Count of matched signal families; strong matches count double."""
    strong = sum(1 for p in _STRONG if p.search(text))
    weak = sum(1 for p in _WEAK if p.search(text))
    return 2 * strong + weak


def injection_suspected(text: str) -> bool:
    return injection_signals(text) >= 2


def lexical_adverse(text: str) -> bool:
    return bool(_ADVERSE.search(text))


def check(text: str, category: str) -> GuardResult:
    adverse = lexical_adverse(text)
    return GuardResult(
        injection_suspected=injection_suspected(text),
        lexical_adverse=adverse,
        contradicts_none=(category == "none" and adverse),
    )
