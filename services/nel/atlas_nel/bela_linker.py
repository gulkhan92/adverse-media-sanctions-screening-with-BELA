"""BELA mention detection and entity linking (inference only).

Vendored BELA code (MIT, facebookresearch/BELA) provides the tokenizer transform and the
neural heads. This module rebuilds the inference path of the ``multiel`` wrapper without
hydra, Lightning, or faiss, and adds what the hybrid router needs: the top-k candidates with
a per-candidate linking score.

The entity index is the one shipped with the checkpoints (Wikidata entities, embeddings
produced by an external model). It cannot be rebuilt for another knowledge base because the
mention encoder is trained against those fixed embeddings (see ADR-004).
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import torch
import torch.nn as nn
from transformers import AutoConfig, AutoModel

VENDOR = Path(__file__).resolve().parents[1] / "vendor"
if str(VENDOR) not in sys.path:
    sys.path.insert(0, str(VENDOR))

from bela.heads import ClassificationHead, MentionScoresHead, SpanEncoder  # noqa: E402
from bela.transforms.joint_el_transform import JointELXlmrRawTextTransform  # noqa: E402

log = logging.getLogger("bela_linker")
SP_DIR = VENDOR / "bela" / "data"
ENCODER_NAME = "xlm-roberta-large"  # architecture only; weights come from the checkpoint


class Encoder(nn.Module):
    """Same module layout as bela.models.hf_encoder.HFEncoder, built without weight download."""

    def __init__(self) -> None:
        super().__init__()
        config = AutoConfig.from_pretrained(ENCODER_NAME)
        self.transformer = AutoModel.from_config(config)
        self.embedding_dim = config.hidden_size

    def forward(self, input_ids: torch.Tensor, attention_mask: torch.Tensor | None = None) -> Any:
        out = self.transformer(input_ids=input_ids, attention_mask=attention_mask)
        return out["last_hidden_state"][:, 0, :], out["last_hidden_state"]


def sub_state(state: dict[str, torch.Tensor], prefix: str) -> dict[str, torch.Tensor]:
    return {k[len(prefix) + 1 :]: v for k, v in state.items() if k.startswith(prefix + ".")}


def sp_to_char_offsets(
    text: str, sp_offsets: list[int], sp_lengths: list[int], boundaries: list[list[int]]
) -> tuple[list[int], list[int]]:
    """Convert sentencepiece token offsets to character offsets (from multiel)."""
    chars = list(text)
    offsets: list[int] = []
    lengths: list[int] = []
    for sp_offset, sp_length in zip(sp_offsets, sp_lengths, strict=True):
        if sp_offset == 0:
            continue
        sp_offset -= 1
        start = boundaries[sp_offset][0]
        end = boundaries[sp_offset + sp_length - 1][1]
        while chars[start].isspace():
            start += 1
        offsets.append(start)
        lengths.append(end - start)
    return offsets, lengths


@dataclass
class LinkedMention:
    start: int
    end: int
    surface: str
    md_score: float
    candidates: list[dict[str, Any]] = field(default_factory=list)  # qid, score, retrieval

    @property
    def qid(self) -> str | None:
        return self.candidates[0]["qid"] if self.candidates else None

    @property
    def el_score(self) -> float:
        return float(self.candidates[0]["score"]) if self.candidates else 0.0


class BelaLinker:
    def __init__(
        self,
        checkpoint: Path,
        embeddings: Path,
        entity_ids: Path,
        device: str | None = None,
        md_threshold: float = 0.2,
        top_k: int = 10,
        chunk_rows: int = 1_000_000,
        gpu_index: bool = True,
    ) -> None:
        self.device = torch.device(
            device or ("mps" if torch.backends.mps.is_available() else "cpu")
        )
        self.md_threshold = md_threshold
        self.top_k = top_k
        self.chunk_rows = chunk_rows
        log.info("loading entity embeddings (memory mapped)")
        self.embeddings = torch.load(embeddings, mmap=True, map_location="cpu")
        self.dim = int(self.embeddings.shape[1])
        self.entity_ids = [line.strip() for line in entity_ids.read_text().splitlines()]
        log.info(f"index: {self.embeddings.shape[0]} entities, dim {self.dim}")
        # Half-precision copy of the index on the accelerator, in row chunks. Candidate scores
        # are recomputed exactly in float32 for the retrieved rows (see ``search``).
        self.index_chunks: list[torch.Tensor] = []
        if gpu_index and self.device.type != "cpu":
            for start in range(0, self.embeddings.shape[0], chunk_rows):
                block = self.embeddings[start : start + chunk_rows]
                self.index_chunks.append(block.half().to(self.device))
            log.info(f"index resident on {self.device}: {len(self.index_chunks)} chunks")

        self.encoder = Encoder()
        self.project = nn.Identity()
        if self.encoder.embedding_dim != self.dim:
            self.project = nn.Sequential(
                nn.Linear(self.encoder.embedding_dim, self.dim), nn.LayerNorm(self.dim)
            )
        self.span_encoder = SpanEncoder(ctxt_output_dim=self.dim, cand_output_dim=self.dim)
        self.mention_encoder = MentionScoresHead(encoder_output_dim=self.dim)
        self.el_encoder = ClassificationHead(ctxt_output_dim=self.dim)

        log.info(f"loading checkpoint {checkpoint.name}")
        state = torch.load(checkpoint, map_location="cpu")["state_dict"]
        encoder_state = sub_state(state, "encoder")
        # Older transformers versions stored position_ids as a weight; it is a fixed buffer now.
        encoder_state.pop("transformer.embeddings.position_ids", None)
        self.encoder.load_state_dict(encoder_state)
        self.span_encoder.load_state_dict(sub_state(state, "span_encoder"))
        proj = sub_state(state, "project_encoder_op")
        if proj:
            self.project.load_state_dict(proj)
        self.mention_encoder.load_state_dict(sub_state(state, "mention_encoder"))
        self.el_encoder.load_state_dict(sub_state(state, "el_encoder"))
        del state

        self.encoder = self.encoder.eval().to(self.device)
        for module in (self.project, self.span_encoder, self.mention_encoder, self.el_encoder):
            module.eval()
        self.transform = JointELXlmrRawTextTransform(
            sp_model_path=str(SP_DIR / "sp_model"),
            vocab_path=str(SP_DIR / "vocab"),
            max_seq_len=256,
        )

    def gather(self, ids: torch.Tensor) -> torch.Tensor:
        """Entity vectors (float32, CPU) for global row ids, from the resident index if present.

        Reading rows from the memory-mapped file means random disk reads (seconds per batch), so
        the half-precision copy on the accelerator is used whenever it exists.
        """
        flat = ids.flatten().cpu()
        if not self.index_chunks:
            return self.embeddings[flat].float()
        out = torch.empty((flat.shape[0], self.dim))
        chunk_of = flat // self.chunk_rows
        for number, chunk in enumerate(self.index_chunks):
            mask = chunk_of == number
            if bool(mask.any()):
                rows = (flat[mask] % self.chunk_rows).to(self.device)
                out[mask] = chunk[rows].float().cpu()
        return out

    def search(self, queries: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        """Top-k inner-product search. Returns exact float32 scores and global row ids.

        Retrieval runs in half precision on the accelerator when the index is resident there,
        otherwise in float32 on the CPU over the memory-mapped file. The retrieved rows are
        then re-scored exactly so downstream thresholds do not depend on the precision used.
        """
        k = self.top_k
        if self.index_chunks:
            q = queries.half().to(self.device)
            best_scores = torch.full((q.shape[0], k), float("-inf"), device=self.device)
            best_ids = torch.zeros((q.shape[0], k), dtype=torch.long, device=self.device)
            offset = 0
            for chunk in self.index_chunks:
                scores = (q @ chunk.T).float()
                top_scores, top_idx = scores.topk(min(k, scores.shape[1]), dim=1)
                merged_scores = torch.cat([best_scores, top_scores], dim=1)
                merged_ids = torch.cat([best_ids, top_idx + offset], dim=1)
                best_scores, order = merged_scores.topk(k, dim=1)
                best_ids = merged_ids.gather(1, order)
                offset += chunk.shape[0]
            ids = best_ids.cpu()
        else:
            queries = queries.float().cpu()
            best_scores = torch.full((queries.shape[0], k), float("-inf"))
            best_ids = torch.zeros((queries.shape[0], k), dtype=torch.long)
            total = self.embeddings.shape[0]
            for start in range(0, total, self.chunk_rows):
                block = self.embeddings[start : start + self.chunk_rows].float()
                scores = queries @ block.T
                top_scores, top_idx = scores.topk(min(k, scores.shape[1]), dim=1)
                merged_scores = torch.cat([best_scores, top_scores], dim=1)
                merged_ids = torch.cat([best_ids, top_idx + start], dim=1)
                best_scores, order = merged_scores.topk(k, dim=1)
                best_ids = merged_ids.gather(1, order)
            ids = best_ids
        rows = self.gather(ids).reshape(ids.shape[0], k, -1)
        exact = torch.einsum("bd,bkd->bk", queries.float().cpu(), rows)
        exact, order = exact.sort(dim=1, descending=True)
        return exact, ids.gather(1, order)

    @torch.no_grad()
    def link(self, texts: list[str]) -> list[list[LinkedMention]]:
        inputs = self.transform({"texts": texts})
        token_ids = inputs["input_ids"].to(self.device)
        pad_mask = inputs["attention_mask"]
        mapping = inputs["tokens_mapping"]
        boundaries = inputs["sp_tokens_boundaries"].tolist()

        _, last = self.encoder(token_ids)
        enc = self.project(last.float().cpu())
        logits, bounds = self.mention_encoder(enc, pad_mask, mapping)
        chosen_logits, chosen_bounds, chosen_mask, _ = self.mention_encoder.prune_ctxt_mentions(
            logits, bounds, num_cand_mentions=50, threshold=self.md_threshold
        )
        offsets = chosen_bounds[:, :, 0]
        lengths = chosen_bounds[:, :, 1] - chosen_bounds[:, :, 0] + 1
        lengths[offsets == 0] = 0
        mention_repr = self.span_encoder(enc, offsets, lengths)
        md_scores = torch.sigmoid(chosen_logits)
        # Only spans that pass the detection threshold are searched and scored.
        keep = (lengths != 0) & (md_scores >= self.md_threshold)
        flat_repr = mention_repr[keep]
        flat_md = md_scores[keep]

        if flat_repr.shape[0]:
            cand_scores, cand_ids = self.search(flat_repr)
            k = cand_ids.shape[1]
            ent_repr = self.gather(cand_ids)
            rep = flat_repr.repeat_interleave(k, dim=0)
            el = torch.sigmoid(
                self.el_encoder(
                    rep,
                    ent_repr,
                    flat_md.repeat_interleave(k).unsqueeze(-1),
                    cand_scores.reshape(-1, 1),
                )
            ).reshape(-1, k)
        results: list[list[LinkedMention]] = []
        row = 0
        for i, text in enumerate(texts):
            sp_off, sp_len, kept = [], [], []
            for j in range(offsets.shape[1]):
                if bool(keep[i, j]):
                    sp_off.append(int(offsets[i, j]))
                    sp_len.append(int(lengths[i, j]))
                    kept.append((row, float(md_scores[i, j])))
                    row += 1
            char_off, char_len = sp_to_char_offsets(text, sp_off, sp_len, boundaries[i])
            mentions = []
            for (r, md), start, length in zip(kept, char_off, char_len, strict=True):
                cands = [
                    {
                        "qid": self.entity_ids[int(cand_ids[r, j])],
                        "score": float(el[r, j]),
                        "retrieval": float(cand_scores[r, j]),
                    }
                    for j in range(cand_ids.shape[1])
                ]
                cands.sort(key=lambda c: c["score"], reverse=True)
                mentions.append(
                    LinkedMention(start, start + length, text[start : start + length], md, cands)
                )
            results.append(mentions)
        return results

    @torch.no_grad()
    def link_spans(
        self, texts: list[str], spans: list[tuple[int, int]]
    ) -> list[LinkedMention | None]:
        """Link a given mention span in each text (disambiguation mode).

        The mention detection score is set to 1.0 because the span is supplied. Returns None for
        a text whose span could not be aligned to tokens.
        """
        batch = {
            "texts": texts,
            "mention_offsets": [[start] for start, _ in spans],
            "mention_lengths": [[end - start] for start, end in spans],
            "entities": [[0] for _ in spans],
        }
        inputs = self.transform(batch)
        token_ids = inputs["input_ids"].to(self.device)
        offsets, lengths = inputs["mention_offsets"], inputs["mention_lengths"]
        _, last = self.encoder(token_ids)
        enc = self.project(last.float().cpu())
        mention_repr = self.span_encoder(enc, offsets, lengths)
        valid = lengths[:, 0] != 0
        out: list[LinkedMention | None] = [None] * len(texts)
        if not bool(valid.any()):
            return out
        flat = mention_repr[:, 0, :][valid]
        cand_scores, cand_ids = self.search(flat)
        k = cand_ids.shape[1]
        ent_repr = self.gather(cand_ids)
        el = torch.sigmoid(
            self.el_encoder(
                flat.repeat_interleave(k, dim=0),
                ent_repr,
                torch.ones(flat.shape[0] * k, 1),
                cand_scores.reshape(-1, 1),
            )
        ).reshape(-1, k)
        row = 0
        for i, (start, end) in enumerate(spans):
            if not bool(valid[i]):
                continue
            cands = [
                {
                    "qid": self.entity_ids[int(cand_ids[row, j])],
                    "score": float(el[row, j]),
                    "retrieval": float(cand_scores[row, j]),
                }
                for j in range(k)
            ]
            cands.sort(key=lambda c: c["score"], reverse=True)
            out[i] = LinkedMention(start, end, texts[i][start:end], 1.0, cands)
            row += 1
        return out
