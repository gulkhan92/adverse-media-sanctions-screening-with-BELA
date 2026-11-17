# ADR-004: Entity linking engine decisions

Status: accepted. Date: 2026-11-13. Supersedes the open question of ADR-001 (not written; the
phase 0 spikes were folded into phase 3).

## Context

Phase 3 builds mention detection and linking (BELA), cross-encoder adjudication (BLINK), and
local LLM adjudication, combined by a router. The plan assumed three things that the spikes
disproved or that needed a different route.

## Decisions

1. **BELA uses the entity index shipped with the checkpoint.** The plan allowed rebuilding an
   index for the project subset by encoding "title plus description" with BELA's entity
   encoder. The released checkpoints contain only the text and mention encoders (one XLM-R
   large model); the entity embeddings (16.5 million Wikidata entities, 300 dimensions) were
   produced by a separate model that is not published, and the mention encoder is trained
   against those fixed vectors. A project-specific index therefore cannot be built. Consequence:
   BELA links to the full multilingual Wikidata index, and the knowledge base in PostgreSQL
   (phase 2) is used for watchlist resolution and NIL matching, not for BELA candidates.
2. **No `multiel` wrapper at runtime.** The wrapper pins `faiss-gpu`, `protobuf==3.20`,
   `fairscale`, and an obsolete hydra API, none of which install on this platform. The needed
   inference classes are vendored from facebookresearch/BELA (MIT) under
   `services/nel/vendor/bela`; the tokenizer transform was changed to use sentencepiece's own
   proto objects, and the Lightning task was replaced by `BelaLinker`. The wrapper's default
   thresholds are kept (mention detection 0.2).
3. **GPU-resident half-precision index with exact re-scoring.** An exact search over the
   19.8 GB float32 index on the CPU takes seconds per batch. The index is held on the
   accelerator in float16 chunks (about 10 GB) and the top 10 rows are re-scored in float32, so
   thresholds do not depend on the retrieval precision. A CPU path over the memory-mapped file
   remains for machines without an accelerator.
4. **BLINK ported, not installed.** The upstream repository is archived and pins old PyTorch.
   `services/blink/atlas_blink/crossencoder.py` reimplements the cross-encoder input format and
   head on current `transformers` and loads the released `crossencoder_wiki_large.bin`
   unchanged (all keys match). BLINK's bi-encoder retriever and its entity index were not run;
   "BLINK alone" in the evaluation therefore means the cross-encoder re-ranking fuzzy-match
   candidates.
5. **Prompt injection is handled outside the model.** A 4 billion parameter model followed
   instructions embedded in article text in 6 of 8 test cases (see docs/eval/report.md).
   Prompt hardening did not fix this. `atlas.nlp.guard` flags instruction-like text and
   contradictions with adverse vocabulary, and such results go to human review instead of being
   trusted. The model output is never the final decision.
6. **Evaluation without the hand-labeled gold set.** The gold set has not been annotated.
   Thresholds are fitted on a dev sample and reported on a test sample of Mewsli-X; adverse-media
   classification accuracy, the planned collision subset, and watchlist false-match reduction
   are not measured and must be added when the gold set exists.

## Consequences

- `nlp_config_v1` is frozen from dev-fitted values on a general-news benchmark; it must be
  re-fitted on the gold set before it is relied on for adverse media.
- The nel service needs about 12 GB of accelerator memory plus the 19.8 GB index file on disk.
