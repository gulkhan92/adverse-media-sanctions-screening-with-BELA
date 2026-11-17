"""Hybrid entity linking pipeline: chunk, link, route, adjudicate, filter by type.

Every decision records the route, all candidates with their scores, and the model and prompt
versions, so the analyst transparency panel can show what was considered. When an adjudicator
is unavailable the mention falls back to the BELA decision and is flagged
``reduced_confidence``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from atlas.nlp.adjudicate import Candidate, Reranker, blink_probabilities, llm_choose
from atlas.nlp.chunking import chunk_text
from atlas.nlp.combiner import CandidateFeatures, pick
from atlas.nlp.config import NlpConfig
from atlas.nlp.entity_info import EntityInfoStore
from atlas.nlp.llm_client import LlmClient, LlmUnavailable
from atlas.nlp.router import Route, route_mention

CONTEXT_CHARS = 220  # characters on each side of a mention for adjudication prompts
KEEP_KINDS = {"person", "organization"}


class Linker(Protocol):
    def link(self, texts: list[str]) -> list[list[Any]]: ...


@dataclass
class MentionDecision:
    start: int
    end: int
    surface: str
    md_score: float
    route: Route
    decision: str
    qid: str | None
    score: float
    reduced_confidence: bool = False
    candidates: list[dict[str, Any]] = field(default_factory=list)
    versions: dict[str, str] = field(default_factory=dict)


class MentionResolver:
    def __init__(
        self,
        cfg: NlpConfig,
        info: EntityInfoStore,
        reranker: Reranker | None = None,
        llm: LlmClient | None = None,
    ) -> None:
        self.cfg = cfg
        self.info = info
        self.reranker = reranker
        self.llm = llm

    def versions(self) -> dict[str, str]:
        c = self.cfg
        return {
            "config": c.version,
            "bela": c.bela_checkpoint,
            "blink": c.blink_model,
            "llm": c.llm_model,
            "adjudication_prompt": c.adjudication_prompt_version,
        }

    def _candidates(self, raw: list[dict[str, Any]], limit: int) -> list[Candidate]:
        out = []
        for c in raw[:limit]:
            meta = self.info.get(c["qid"])
            out.append(Candidate(c["qid"], meta.title, meta.description, float(c["score"])))
        return out

    def resolve(
        self, text: str, start: int, end: int, lang: str, md_score: float, raw: list[dict[str, Any]]
    ) -> MentionDecision:
        scores = [float(c["score"]) for c in raw]
        route = route_mention(scores, lang, self.cfg)
        base = MentionDecision(
            start,
            end,
            text[start:end],
            md_score,
            route.route,
            "nil",
            None,
            route.top_score,
            candidates=[
                {
                    "rank": i + 1,
                    "qid": c["qid"],
                    "bela_score": float(c["score"]),
                    "blink_score": None,
                    "llm_label": None,
                }
                for i, c in enumerate(raw)
            ],
            versions=self.versions(),
        )
        if route.route == "nil":
            return base
        if route.route == "accept":
            base.decision, base.qid = "accepted", raw[0]["qid"]
            return base
        try:
            if route.route == "blink":
                return self._blink(base, text, raw, lang)
            return self._llm(base, text, raw)
        except (LlmUnavailable, RuntimeError, OSError):
            return self._fallback(base, raw)

    def _fallback(self, d: MentionDecision, raw: list[dict[str, Any]]) -> MentionDecision:
        d.reduced_confidence = True
        d.decision = "fallback_bela"
        d.qid = raw[0]["qid"] if raw and float(raw[0]["score"]) >= self.cfg.nil_el else None
        return d

    def _blink(
        self, d: MentionDecision, text: str, raw: list[dict[str, Any]], lang: str
    ) -> MentionDecision:
        if self.reranker is None:
            raise RuntimeError("no reranker")
        cands = self._candidates(raw, self.cfg.blink_candidates)
        probs = blink_probabilities(
            self.reranker,
            text[: d.start][-CONTEXT_CHARS:],
            d.surface,
            text[d.end :][:CONTEXT_CHARS],
            cands,
        )
        for row in d.candidates:
            if row["qid"] in probs:
                row["blink_score"] = probs[row["qid"]]
        features = [
            (c.qid, CandidateFeatures(c.bela_score, i, probs[c.qid], None, 0.0))
            for i, c in enumerate(cands)
        ]
        return self._decide(d, features, probs, "adjudicated_blink")

    def _llm(self, d: MentionDecision, text: str, raw: list[dict[str, Any]]) -> MentionDecision:
        if self.llm is None:
            raise RuntimeError("no llm")
        cands = self._candidates(raw, self.cfg.llm_candidates)
        window = text[max(0, d.start - CONTEXT_CHARS) : d.end + CONTEXT_CHARS]
        qid, confidence, _ = llm_choose(self.llm, window, d.surface, cands)
        for row in d.candidates:
            if row["qid"] == qid:
                row["llm_label"] = "chosen"
            elif row["qid"] in {c.qid for c in cands}:
                row["llm_label"] = "rejected"
        features = [
            (c.qid, CandidateFeatures(c.bela_score, i, None, c.qid == qid, confidence))
            for i, c in enumerate(cands)
        ]
        return self._decide(d, features, None, "adjudicated_llm", llm_qid=qid)

    def _decide(
        self,
        d: MentionDecision,
        features: list[tuple[str, CandidateFeatures]],
        blink: dict[str, float] | None,
        label: str,
        llm_qid: str | None = None,
    ) -> MentionDecision:
        weights = self.cfg.combiner
        threshold = self.cfg.adjudication_nil_threshold
        if weights:
            qid, probability = pick(weights, features, threshold)
        elif blink is not None:  # no fitted combiner: trust the cross-encoder
            qid, probability = max(blink.items(), key=lambda kv: kv[1])
        else:  # no fitted combiner: trust the LLM choice
            qid, probability = llm_qid, 1.0 if llm_qid else 0.0
        d.decision, d.qid, d.score = label, qid, probability
        return d


@dataclass
class ArticleResult:
    decisions: list[MentionDecision]
    filtered: int  # mentions dropped by the type filter


class NlpPipeline:
    def __init__(self, linker: Linker, resolver: MentionResolver) -> None:
        self.linker = linker
        self.resolver = resolver

    def process(self, text: str, lang: str) -> ArticleResult:
        cfg = self.resolver.cfg
        chunks = chunk_text(text, lang, cfg.max_chunk_tokens, cfg.chunk_overlap_sentences)
        linked = self.linker.link([c.text for c in chunks])
        decisions: list[MentionDecision] = []
        seen: set[tuple[int, int]] = set()
        for chunk, mentions in zip(chunks, linked, strict=True):
            for m in mentions:
                span = (chunk.start + m.start, chunk.start + m.end)
                if span in seen:  # overlapping chunks report the same mention twice
                    continue
                seen.add(span)
                d = self.resolver.resolve(
                    chunk.text, m.start, m.end, lang, m.md_score, m.candidates
                )
                d.start, d.end = span
                decisions.append(d)
        kept, dropped = self.type_filter(decisions)
        return ArticleResult(kept, dropped)

    def type_filter(self, decisions: list[MentionDecision]) -> tuple[list[MentionDecision], int]:
        """Keep persons and organizations. Unlinked mentions are kept for NIL matching."""
        kept = []
        for d in decisions:
            if d.qid is None or self.resolver.info.get(d.qid).kind in KEEP_KINDS:
                kept.append(d)
        return kept, len(decisions) - len(kept)
