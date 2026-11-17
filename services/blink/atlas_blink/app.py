"""BLINK cross-encoder service. POST /v1/rerank scores candidates for a mention (English)."""

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

from atlas_blink.crossencoder import BlinkCrossEncoder

MODELS = Path(os.environ.get("MODELS_DIR", Path(__file__).resolve().parents[3] / "models"))
MAX_QUEUE = int(os.environ.get("BLINK_MAX_QUEUE", "32"))
REQUESTS = Counter("blink_requests_total", "Rerank requests", ["status"])
LATENCY = Histogram("blink_rerank_seconds", "Rerank latency in seconds")
QUEUE = Gauge("blink_queue_depth", "Requests waiting or running")
state: dict[str, Any] = {}


class CandidateIn(BaseModel):
    qid: str
    title: str
    description: str = ""


class RerankIn(BaseModel):
    context_left: str = Field(max_length=2000)
    mention: str = Field(max_length=200)
    context_right: str = Field(max_length=2000)
    candidates: list[CandidateIn] = Field(max_length=50)


class ScoredCandidate(BaseModel):
    qid: str
    score: float


class RerankOut(BaseModel):
    scores: list[ScoredCandidate]
    model: str = "crossencoder_wiki_large"


@asynccontextmanager
async def lifespan(_: FastAPI) -> Any:
    state["model"] = BlinkCrossEncoder(
        MODELS / "blink" / "crossencoder_wiki_large.bin",
        MODELS / "blink" / "crossencoder_wiki_large.json",
    )
    state["lock"] = asyncio.Lock()
    yield


app = FastAPI(title="Atlas BLINK service", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok" if "model" in state else "loading"}


@app.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/v1/rerank", response_model=RerankOut)
async def rerank(body: RerankIn) -> RerankOut:
    if QUEUE._value.get() >= MAX_QUEUE:  # noqa: SLF001 - prometheus gauge read
        REQUESTS.labels("rejected").inc()
        raise HTTPException(status_code=503, detail="queue full", headers={"Retry-After": "1"})
    QUEUE.inc()
    started = time.perf_counter()
    try:
        async with state["lock"]:
            scores = await asyncio.get_running_loop().run_in_executor(
                None,
                state["model"].score,
                body.context_left,
                body.mention,
                body.context_right,
                [(c.title, c.description) for c in body.candidates],
            )
        REQUESTS.labels("ok").inc()
        LATENCY.observe(time.perf_counter() - started)
        return RerankOut(
            scores=[
                ScoredCandidate(qid=c.qid, score=s)
                for c, s in zip(body.candidates, scores, strict=True)
            ]
        )
    finally:
        QUEUE.dec()
