"""BLINK cross-encoder (MIT, facebookresearch/BLINK) re-implemented on current transformers.

The upstream repository is archived and pins old PyTorch. This module keeps the model and the
input format and loads the released ``crossencoder_wiki_large.bin`` weights:

  context  [CLS] left [unused0] mention [unused1] right [SEP]      padded to 32 tokens
  candidate title [unused2] description [SEP]                      padded to 128 tokens
  segment ids are 0 for the context part and 1 for the candidate part (positions 32 and up)
  score = linear(pooled [CLS]) with out_dim 1; a higher value means a better match

English only (bert-large-uncased).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import torch
import torch.nn as nn
from transformers import BertConfig, BertModel, BertTokenizerFast

ENT_START, ENT_END, ENT_TITLE = "[unused0]", "[unused1]", "[unused2]"


@dataclass
class Params:
    max_context_length: int = 32
    max_cand_length: int = 128
    max_seq_length: int = 160
    bert_model: str = "bert-large-uncased"

    @classmethod
    def from_json(cls, path: Path) -> Params:
        data = json.loads(path.read_text())
        return cls(
            data["max_context_length"],
            data["max_cand_length"],
            data["max_seq_length"],
            data["bert_model"],
        )


class CrossEncoderModule(nn.Module):
    def __init__(self, bert: BertModel) -> None:
        super().__init__()
        self.bert_model = bert
        self.additional_linear = nn.Linear(bert.config.hidden_size, 1)

    def forward(
        self, token_ids: torch.Tensor, segment_ids: torch.Tensor, attention_mask: torch.Tensor
    ) -> torch.Tensor:
        pooled = self.bert_model(
            input_ids=token_ids, token_type_ids=segment_ids, attention_mask=attention_mask
        ).pooler_output
        return self.additional_linear(pooled).squeeze(-1)


def load_state(path: Path) -> dict[str, torch.Tensor]:
    """Read the released weights and map keys to this module's layout."""
    raw = torch.load(path, map_location="cpu")
    state = raw.get("state_dict", raw) if isinstance(raw, dict) else raw
    mapped: dict[str, torch.Tensor] = {}
    for key, value in state.items():
        for prefix in ("module.", "model.", "encoder."):
            while key.startswith(prefix):
                key = key[len(prefix) :]
        mapped[key] = value
    return mapped


class BlinkCrossEncoder:
    def __init__(self, weights: Path, config: Path, device: str | None = None) -> None:
        self.params = Params.from_json(config)
        self.device = torch.device(
            device or ("mps" if torch.backends.mps.is_available() else "cpu")
        )
        self.tokenizer = BertTokenizerFast.from_pretrained(
            self.params.bert_model, do_lower_case=True
        )
        # Architecture only; the weights come from the BLINK file below.
        bert = BertModel(BertConfig.from_pretrained(self.params.bert_model))
        self.module = CrossEncoderModule(bert)
        state = load_state(weights)
        missing, unexpected = self.module.load_state_dict(state, strict=False)
        critical = [k for k in missing if "position_ids" not in k]
        if critical:
            raise RuntimeError(f"missing keys when loading BLINK weights: {critical[:5]}")
        self.unexpected = [k for k in unexpected if "position_ids" not in k]
        self.module = self.module.eval().to(self.device)

    def tokens(self, text: str) -> list[str]:
        return self.tokenizer.tokenize(text) if text else []

    def context_ids(self, left: str, mention: str, right: str) -> list[int]:
        limit = self.params.max_context_length
        mention_tokens = [ENT_START, *self.tokens(mention), ENT_END]
        left_t, right_t = self.tokens(left), self.tokens(right)
        left_quota = (limit - len(mention_tokens)) // 2 - 1
        right_quota = limit - len(mention_tokens) - left_quota - 2
        if len(left_t) <= left_quota:
            if len(right_t) > right_quota:
                right_quota += left_quota - len(left_t)
        elif len(right_t) <= right_quota:
            left_quota += right_quota - len(right_t)
        body = (left_t[-left_quota:] if left_quota > 0 else []) + mention_tokens
        body += right_t[: max(right_quota, 0)]
        tokens = ["[CLS]", *body, "[SEP]"][:limit]
        ids = self.tokenizer.convert_tokens_to_ids(tokens)
        return ids + [0] * (limit - len(ids))

    def candidate_ids(self, title: str, description: str) -> list[int]:
        limit = self.params.max_cand_length
        tokens = self.tokens(description)
        if title:
            tokens = [*self.tokens(title), ENT_TITLE, *tokens]
        tokens = [*tokens[: limit - 2], "[SEP]"]  # the leading [CLS] is dropped for the pair
        ids = self.tokenizer.convert_tokens_to_ids(tokens)
        return ids + [0] * (limit - len(ids))

    @torch.no_grad()
    def score(
        self, left: str, mention: str, right: str, candidates: list[tuple[str, str]]
    ) -> list[float]:
        """Scores for (title, description) candidates, in input order."""
        if not candidates:
            return []
        context = self.context_ids(left, mention, right)
        rows = [context + self.candidate_ids(title, desc) for title, desc in candidates]
        token_ids = torch.tensor(rows, device=self.device)
        segments = torch.zeros_like(token_ids)
        segments[:, self.params.max_context_length :] = 1
        mask = (token_ids != 0).long()
        return [float(s) for s in self.module(token_ids, segments, mask).cpu()]
