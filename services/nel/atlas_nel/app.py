"""BELA entity linking service.

POST /v1/link   mention detection and linking for [{id, text, lang}]
POST /v1/embed  bge-m3 embeddings for short texts (names)
GET  /health, /metrics
"""

from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from pydantic import BaseModel, Field

from atlas_nel.bela_linker import BelaLinker

MODELS = Path(os.environ.get("MODELS_DIR", Path(__file__).resolve().parents[3] / "models"))
CHECKPOINT = os.environ.get("BELA_CHECKPOINT", "model_wiki.ckpt")
MAX_QUEUE = int(os.environ.get("NEL_MAX_QUEUE", "16"))
MAX_BATCH = 64
REQUESTS = Counter("nel_requests_total", "Link requests", ["status"])
PASSAGES = Counter("nel_passages_total", "Passages processed")
LATENCY = Histogram("nel_link_seconds", "Link latency in seconds")
QUEUE = Gauge("nel_queue_depth", "Requests waiting or running")
state: dict[str, Any] = {}


class Passage(BaseModel):
    id: str
    text: str = Field(max_length=6000)
    lang: str = "und"


class LinkIn(BaseModel):
    passages: list[Passage] = Field(max_length=MAX_BATCH)


class CandidateOut(BaseModel):
    qid: str
    score: float
    retrieval: float


class MentionOut(BaseModel):
    start: int
    end: int
    surface: str
    qid: str | None
    md_score: float
    el_score: float
    candidates: list[CandidateOut]


class PassageOut(BaseModel):
    id: str
    mentions: list[MentionOut]


class LinkOut(BaseModel):
    results: list[PassageOut]
    model: str


class EmbedIn(BaseModel):
    texts: list[str] = Field(max_length=512)


class EmbedOut(BaseModel):
    model: str
    dim: int
    embeddings: list[list[float]]


@asynccontextmanager
async def lifespan(_: FastAPI) -> Any:
    state["linker"] = BelaLinker(
        MODELS / "bela" / CHECKPOINT,
        MODELS / "bela" / "embeddings.pt",
        MODELS / "bela" / "index.txt",
    )
    state["lock"] = asyncio.Lock()
    yield


app = FastAPI(title="Atlas NEL service", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok" if "linker" in state else "loading"}


@app.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/v1/link", response_model=LinkOut)
async def link(body: LinkIn) -> LinkOut:
    if QUEUE._value.get() >= MAX_QUEUE:  # noqa: SLF001 - prometheus gauge read
        REQUESTS.labels("rejected").inc()
        raise HTTPException(status_code=503, detail="queue full", headers={"Retry-After": "1"})
    QUEUE.inc()
    started = time.perf_counter()
    try:
        async with state["lock"]:
            mentions = await asyncio.get_running_loop().run_in_executor(
                None, state["linker"].link, [p.text for p in body.passages]
            )
        REQUESTS.labels("ok").inc()
        PASSAGES.inc(len(body.passages))
        LATENCY.observe(time.perf_counter() - started)
    finally:
        QUEUE.dec()
    results = [
        PassageOut(
            id=p.id,
            mentions=[
                MentionOut(
                    start=m.start,
                    end=m.end,
                    surface=m.surface,
                    qid=m.qid,
                    md_score=m.md_score,
                    el_score=m.el_score,
                    candidates=[CandidateOut(**c) for c in m.candidates],
                )
                for m in found
            ],
        )
        for p, found in zip(body.passages, mentions, strict=True)
    ]
    return LinkOut(results=results, model=CHECKPOINT)


@app.post("/v1/embed", response_model=EmbedOut)
async def embed(body: EmbedIn) -> EmbedOut:
    if "embedder" not in state:
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer("BAAI/bge-m3", device=str(state["linker"].device))
        model.max_seq_length = 64
        state["embedder"] = model
    async with state["lock"]:
        vectors = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: state["embedder"].encode(
                body.texts, batch_size=128, normalize_embeddings=True, convert_to_numpy=True
            ),
        )
    return EmbedOut(model="BAAI/bge-m3", dim=int(vectors.shape[1]), embeddings=vectors.tolist())
