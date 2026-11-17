"""Versioned NLP configuration: thresholds, model names, and prompt versions.

The values in ``NlpConfig`` are the initial thresholds from the plan. After evaluation the
calibrated values are written to ``config/nlp_config_v1.json`` and loaded from there, so the
routing behaviour of a release is fixed by a versioned file rather than by code.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parent / "config"


@dataclass
class NlpConfig:
    version: str = "nlp_config_v0_initial"
    # Mention detection (BELA wrapper default is 0.2).
    md_threshold: float = 0.20
    # Routing on the top-1 linking score and the margin to the second candidate.
    accept_el: float = 0.70
    accept_margin: float = 0.15
    nil_el: float = 0.40
    # Adjudication.
    blink_candidates: int = 10
    llm_candidates: int = 5
    adjudication_nil_threshold: float = 0.50
    # Combiner (logistic model over candidate features); filled in by calibration.
    combiner: dict[str, object] = field(default_factory=dict)
    # Isotonic calibration curves per route: {"accept": {x, y}, "adjudicated": {x, y}}.
    calibration: dict[str, object] = field(default_factory=dict)
    # Name-based matching for unlinked mentions.
    nil_match_threshold: float = 0.88
    # Chunking.
    max_chunk_tokens: int = 256
    chunk_overlap_sentences: int = 1
    # Model identifiers recorded on every stored decision.
    bela_checkpoint: str = "model_wiki.ckpt"
    llm_model: str = "Qwen3-4B-Instruct-2507-Q4_K_M"
    blink_model: str = "crossencoder_wiki_large"
    adjudication_prompt_version: str = "adjudicate-v1"
    adverse_prompt_version: str = "adverse-v1"
    narrative_prompt_version: str = "narrative-v1"

    def save(self, path: Path | None = None) -> Path:
        path = path or CONFIG_DIR / f"{self.version}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2, sort_keys=True) + "\n")
        return path

    @classmethod
    def load(cls, version: str = "nlp_config_v1") -> NlpConfig:
        path = CONFIG_DIR / f"{version}.json"
        if not path.exists():
            return cls()
        return cls(**json.loads(path.read_text()))
