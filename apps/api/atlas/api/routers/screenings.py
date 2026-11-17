"""Screenings (single, batch, text) and subjects."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from typing import Any

import httpx
from fastapi import APIRouter, File, Header, Query, Response, UploadFile
from psycopg.types.json import Jsonb

from atlas.api import batch as batchmod
from atlas.api import report as reportmod
from atlas.api.deps import Authorized, Ctx
from atlas.api.errors import PROBLEM_RESPONSES, ApiError, bad_request, not_found
from atlas.api.monitoring import scan_subject
from atlas.api.pagination import clamp_limit, decode_cursor, encode_cursor
from atlas.api.rbac import SCREEN, VIEW
from atlas.api.schemas import (
    DISCLAIMER,
    BatchRequest,
    MonitorRequest,
    ScreeningRequest,
    SubjectIn,
    SubjectPatch,
    TextScreeningRequest,
)
from atlas.api.screening import (
    find_or_create_subject,
    meter_usage,
    run_screening,
    subject_secrets,
)
from atlas.names import normalize_name

screenings = APIRouter(prefix="/screenings", tags=["screenings"], responses=PROBLEM_RESPONSES)
subjects = APIRouter(prefix="/subjects", tags=["subjects"], responses=PROBLEM_RESPONSES)

SUBJECT_SQL = (
    "SELECT id, kind, name, country, aliases, monitored, created_at, "
    "CASE WHEN dob_enc IS NULL THEN NULL ELSE pgp_sym_decrypt(dob_enc, %s) END AS dob "
    "FROM subjects WHERE id = %s AND deleted_at IS NULL"
)


def _subject_for(
    ctx: Ctx, body: ScreeningRequest
) -> tuple[dict[str, Any], str | None, list[dict[str, str]]]:
    key = ctx.state.settings.data_key
    if body.subject_id:
        row = ctx.conn.execute(SUBJECT_SQL, (key, body.subject_id)).fetchone()
        if row is None:
            raise not_found("Subject")
        dob, identifiers = subject_secrets(ctx.conn, body.subject_id, key)
        return row, dob, identifiers
    assert body.subject is not None
    data = body.subject.model_dump()
    row = find_or_create_subject(ctx.conn, ctx.tenant_id, data, key)
    return row, data.get("dob"), data.get("identifiers") or []


@screenings.post("", status_code=201, summary="Screen one subject against the watchlists")
def create_screening(body: ScreeningRequest, ctx: Ctx = Authorized(SCREEN)) -> dict[str, Any]:
    subject, dob, identifiers = _subject_for(ctx, body)
    result = run_screening(
        ctx.conn,
        ctx.tenant_id,
        subject,
        dob,
        ctx.principal.user_id,
        None,
        identifiers or None,
        ctx.state.extras.get("embedder"),
    )
    meter_usage(ctx.conn, ctx.tenant_id, 1, 0)
    ctx.audit(
        "screening.created",
        str(result["id"]),
        {"risk_level": result["risk_level"], "matches": len(result["matches"])},
    )
    return {**result, "disclaimer": DISCLAIMER}


@screenings.get("/batch/template", summary="CSV template for batch upload")
def batch_template(ctx: Ctx = Authorized(SCREEN)) -> Response:
    return Response(
        batchmod.template_csv(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="screening-template.csv"'},
    )


@screenings.post("/batch/upload", status_code=202, summary="Upload a CSV of up to 10,000 subjects")
async def upload_batch(
    file: UploadFile = File(...),
    ctx: Ctx = Authorized(SCREEN),
) -> dict[str, Any]:
    limit = ctx.state.settings.max_batch_body_bytes
    content = await file.read(limit + 1)
    if len(content) > limit:
        raise ApiError(413, "payload-too-large", "Payload too large", "The file is too large.")
    try:
        valid, rejected = batchmod.parse_csv(content)
    except batchmod.CsvError as error:
        raise bad_request(str(error), "invalid-csv") from None
    tenant_id = ctx.tenant_id
    batch_id = batchmod.create_batch(
        ctx.conn,
        tenant_id,
        ctx.principal.user_id,
        file.filename or "upload.csv",
        valid,
        rejected,
        ctx.state.settings.data_key,
    )
    ctx.audit(
        "screening.batch_uploaded", str(batch_id), {"rows": len(valid), "rejected": len(rejected)}
    )
    if ctx.state.settings.celery_broker_url:
        from atlas.jobs.celery_app import send

        ctx.after_commit(lambda: send("atlas.process_batch", str(tenant_id), str(batch_id)))
    else:
        state = ctx.state
        ctx.after_commit(lambda: batchmod.process_batch(state, tenant_id, batch_id))
    return {
        "id": batch_id,
        "status": "queued",
        "accepted_rows": len(valid),
        "rejected_rows": [{"row": n, "error": e} for n, e in rejected[:50]],
        "disclaimer": DISCLAIMER,
    }


@screenings.get("/batch/{batch_id}", summary="Batch progress, failures, and results")
def get_batch(batch_id: uuid.UUID, ctx: Ctx = Authorized(VIEW)) -> dict[str, Any]:
    batch = batchmod.batch_status(ctx.conn, batch_id)
    if batch is None:
        raise not_found("Batch")
    results = ctx.conn.execute(
        "SELECT s.id, s.subject_id, sub.name, s.risk_score, s.risk_level FROM screenings s "
        "JOIN subjects sub ON sub.id = s.subject_id WHERE s.batch_id = %s "
        "ORDER BY s.created_at, s.id LIMIT 1000",
        (batch_id,),
    ).fetchall()
    return {**batch, "results": results, "disclaimer": DISCLAIMER}


@screenings.get("/batch/{batch_id}/export", summary="Batch results as CSV")
def export_batch(batch_id: uuid.UUID, ctx: Ctx = Authorized(VIEW)) -> Response:
    if batchmod.batch_status(ctx.conn, batch_id) is None:
        raise not_found("Batch")
    ctx.audit("screening.batch_exported", str(batch_id))
    body = batchmod.export_csv(ctx.conn, batch_id)
    return Response(
        body,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="batch-{batch_id}.csv"'},
    )


@screenings.post("/batch", status_code=201, summary="Screen up to 1000 subjects")
def create_batch(
    body: BatchRequest,
    response: Response,
    idempotency_key: str = Header(min_length=8, max_length=100, alias="Idempotency-Key"),
    ctx: Ctx = Authorized(SCREEN),
) -> Any:
    request_hash = hashlib.sha256(
        json.dumps(body.model_dump(mode="json"), sort_keys=True).encode()
    ).hexdigest()
    tenant_id = ctx.tenant_id
    prior = ctx.conn.execute(
        "SELECT request_hash, status_code, response FROM idempotency_keys "
        "WHERE tenant_id = %s AND key = %s",
        (tenant_id, idempotency_key),
    ).fetchone()
    if prior:
        if prior["request_hash"] != request_hash:
            raise ApiError(
                422,
                "idempotency-conflict",
                "Unprocessable",
                "This Idempotency-Key was already used with a different request.",
            )
        response.headers["Idempotent-Replay"] = "true"
        response.status_code = prior["status_code"]
        return prior["response"]
    batch = ctx.conn.execute(
        "INSERT INTO screening_batches (tenant_id, status, total, requested_by) "
        "VALUES (%s, 'running', %s, %s) RETURNING id",
        (tenant_id, len(body.subjects), ctx.principal.user_id),
    ).fetchone()
    assert batch is not None
    key = ctx.state.settings.data_key
    results = []
    for item in body.subjects:
        data = item.model_dump()
        subject = find_or_create_subject(ctx.conn, tenant_id, data, key)
        screening = run_screening(
            ctx.conn,
            tenant_id,
            subject,
            data.get("dob"),
            ctx.principal.user_id,
            batch["id"],
            data.get("identifiers") or None,
            ctx.state.extras.get("embedder"),
        )
        results.append(
            {
                "id": screening["id"],
                "subject_id": subject["id"],
                "name": subject["name"],
                "risk_score": screening["risk_score"],
                "risk_level": screening["risk_level"],
            }
        )
    ctx.conn.execute(
        "UPDATE screening_batches SET status = 'completed', completed = %s, finished_at = now() "
        "WHERE id = %s",
        (len(results), batch["id"]),
    )
    meter_usage(ctx.conn, tenant_id, 0, len(results))
    payload = json.loads(
        json.dumps(
            {
                "id": batch["id"],
                "status": "completed",
                "total": len(results),
                "completed": len(results),
                "results": results,
                "disclaimer": DISCLAIMER,
            },
            default=str,
        )
    )
    ctx.conn.execute(
        "INSERT INTO idempotency_keys (tenant_id, key, request_hash, status_code, response) "
        "VALUES (%s, %s, %s, 201, %s)",
        (tenant_id, idempotency_key, request_hash, Jsonb(payload)),
    )
    ctx.audit("screening.batch_created", str(batch["id"]), {"total": len(results)})
    return payload


@screenings.post("/text", summary="Link entities in pasted text and check watchlists")
def screen_text(body: TextScreeningRequest, ctx: Ctx = Authorized(SCREEN)) -> dict[str, Any]:
    url = ctx.state.settings.nel_url
    try:
        reply = httpx.post(
            f"{url}/v1/link",
            json={"passages": [{"id": "p1", "text": body.text, "lang": body.language or "en"}]},
            timeout=60,
        )
        reply.raise_for_status()
        mentions = reply.json().get("passages", [{}])[0].get("mentions", [])
    except (httpx.HTTPError, ValueError, KeyError, IndexError):
        raise ApiError(
            503,
            "nel-unavailable",
            "Service unavailable",
            "The entity linking service is not reachable.",
            headers={"Retry-After": "30"},
        ) from None
    qids = sorted({m["qid"] for m in mentions if m.get("qid")})
    hits = []
    if qids:
        hits = ctx.conn.execute(
            "SELECT e.qid, e.name AS entity, w.id AS entry_id, w.name AS watchlist_name, "
            "s.code AS source, l.status FROM entities e "
            "JOIN watchlist_entity_links l ON l.entity_id = e.id AND l.status IN "
            "('accepted', 'review') JOIN watchlist_entries w ON w.id = l.entry_id "
            "JOIN watchlist_sources s ON s.id = w.source_id WHERE e.qid = ANY(%s)",
            (qids,),
        ).fetchall()
    ctx.audit("screening.text", None, {"chars": len(body.text), "mentions": len(mentions)})
    return {
        "mentions": [
            {k: m.get(k) for k in ("start", "end", "surface", "qid", "md_score", "el_score")}
            for m in mentions
        ],
        "watchlist_hits": hits,
        "disclaimer": DISCLAIMER,
    }


def _screening_detail(ctx: Ctx, screening_id: uuid.UUID) -> dict[str, Any]:
    row = ctx.conn.execute(
        "SELECT s.id, s.subject_id, sub.name, sub.kind, sub.country, sub.monitored, s.risk_score, "
        "s.risk_level, s.score_breakdown, s.config_version, s.created_at, s.batch_id "
        "FROM screenings s JOIN subjects sub ON sub.id = s.subject_id WHERE s.id = %s",
        (screening_id,),
    ).fetchone()
    if row is None:
        raise not_found("Screening")
    row["matches"] = ctx.conn.execute(
        "SELECT m.id, m.match_type, m.entry_id, m.mention_id, m.confidence, m.features, "
        "w.name AS watchlist_name, src.code AS source, f.evidence_sentence AS evidence, "
        "f.category, f.severity, a.title AS article_title, a.url AS article_url, "
        "a.published_at AS article_published_at FROM screening_matches m "
        "LEFT JOIN watchlist_entries w ON w.id = m.entry_id "
        "LEFT JOIN watchlist_sources src ON src.id = w.source_id "
        "LEFT JOIN mentions mn ON mn.id = m.mention_id "
        "LEFT JOIN articles a ON a.id = mn.article_id "
        "LEFT JOIN LATERAL (SELECT evidence_sentence, category, severity FROM adverse_findings "
        "WHERE mention_id = mn.id ORDER BY severity DESC LIMIT 1) f ON true "
        "WHERE m.screening_id = %s ORDER BY m.confidence DESC",
        (screening_id,),
    ).fetchall()
    return row


@screenings.get("/{screening_id}", summary="One screening with its matches and score breakdown")
def get_screening(screening_id: uuid.UUID, ctx: Ctx = Authorized(VIEW)) -> dict[str, Any]:
    return {**_screening_detail(ctx, screening_id), "disclaimer": DISCLAIMER}


@screenings.get("/{screening_id}/report.pdf", summary="PDF screening report")
def screening_report(screening_id: uuid.UUID, ctx: Ctx = Authorized(VIEW)) -> Response:
    detail = _screening_detail(ctx, screening_id)
    decisions = ctx.conn.execute(
        "SELECT a.id AS alert_id, e.to_status AS status, e.reason, u.email AS reviewer, "
        "e.created_at AS at FROM alert_events e JOIN alerts a ON a.id = e.alert_id "
        "LEFT JOIN users u ON u.id = e.actor_id WHERE a.subject_id = %s "
        "AND e.event_type = 'status_change' AND e.to_status IN "
        "('true_match', 'false_positive', 'closed', 'escalated') ORDER BY e.created_at",
        (detail["subject_id"],),
    ).fetchall()
    evidence = [
        {
            "url": m["article_url"],
            "title": m["article_title"],
            "published_at": m["article_published_at"],
            "evidence": m["evidence"],
            "category": m["category"],
            "severity": m["severity"],
            "confidence": m["confidence"],
        }
        for m in detail["matches"]
        if m["match_type"] == "adverse_media"
    ]
    data = {
        "screening": detail,
        "subject": {k: detail[k] for k in ("name", "kind", "country", "monitored")},
        "matches": detail["matches"]
        and [
            {**m, "features": {**m["features"], "name": m["features"].get("name")}}
            for m in detail["matches"]
        ],
        "evidence": evidence,
        "decisions": decisions,
    }
    report_id = uuid.uuid4()
    generated = dt.datetime.now(dt.UTC)
    pdf = reportmod.render_pdf(data, report_id, generated)
    import hashlib as _hashlib

    ctx.conn.execute(
        "INSERT INTO reports (id, tenant_id, screening_id, created_by, sha256) "
        "VALUES (%s, %s, %s, %s, %s)",
        (
            report_id,
            ctx.tenant_id,
            screening_id,
            ctx.principal.user_id,
            _hashlib.sha256(pdf).hexdigest(),
        ),
    )
    ctx.audit("report.generated", str(report_id), {"screening": str(screening_id)})
    return Response(
        pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="screening-report-{report_id}.pdf"',
            "X-Report-ID": str(report_id),
        },
    )


# --- Subjects ----------------------------------------------------------------------------------


@subjects.get("", summary="List subjects")
def list_subjects(
    q: str | None = Query(default=None, max_length=100),
    monitored: bool | None = None,
    cursor: str | None = None,
    limit: int = 25,
    ctx: Ctx = Authorized(VIEW),
) -> dict[str, Any]:
    limit = clamp_limit(limit)
    after = decode_cursor(cursor, 2)
    sql = (
        "SELECT id, kind, name, country, monitored, created_at FROM subjects "
        "WHERE deleted_at IS NULL"
    )
    params: list[Any] = []
    if q:
        sql += " AND name_norm ILIKE %s"
        params.append(f"%{normalize_name(q, 'person').full}%")
    if monitored is not None:
        sql += " AND monitored = %s"
        params.append(monitored)
    if after:
        sql += " AND (created_at, id) < (%s::timestamptz, %s::uuid)"
        params += after
    sql += " ORDER BY created_at DESC, id DESC LIMIT %s"
    rows = ctx.conn.execute(sql, [*params, limit + 1]).fetchall()
    page = rows[:limit]
    nxt = (
        encode_cursor([page[-1]["created_at"].isoformat(), str(page[-1]["id"])])
        if len(rows) > limit
        else None
    )
    return {"items": page, "next_cursor": nxt}


@subjects.post("", status_code=201, summary="Create a subject")
def create_subject(body: SubjectIn, ctx: Ctx = Authorized(SCREEN)) -> dict[str, Any]:
    row = find_or_create_subject(
        ctx.conn, ctx.tenant_id, body.model_dump(), ctx.state.settings.data_key
    )
    ctx.audit("subject.created", str(row["id"]))
    return {"id": row["id"], "name": row["name"], "kind": row["kind"], "country": row["country"]}


def _own_subject(ctx: Ctx, subject_id: uuid.UUID) -> dict[str, Any]:
    row = ctx.conn.execute(
        SUBJECT_SQL,
        (ctx.state.settings.data_key, subject_id),
    ).fetchone()
    if row is None:
        raise not_found("Subject")
    return row


@subjects.get("/{subject_id}", summary="One subject")
def get_subject(subject_id: uuid.UUID, ctx: Ctx = Authorized(VIEW)) -> dict[str, Any]:
    return _own_subject(ctx, subject_id)


@subjects.patch("/{subject_id}", summary="Update a subject")
def patch_subject(
    subject_id: uuid.UUID, body: SubjectPatch, ctx: Ctx = Authorized(SCREEN)
) -> dict[str, Any]:
    current = _own_subject(ctx, subject_id)
    data = body.model_dump(exclude_unset=True)
    if not data:
        raise bad_request("No fields to update.")
    name = data.get("name", current["name"])
    norm = normalize_name(name, current["kind"])
    ctx.conn.execute(
        "UPDATE subjects SET name = %s, name_norm = %s, country = %s, aliases = %s, "
        "dob_enc = CASE WHEN %s::text IS NULL THEN dob_enc ELSE pgp_sym_encrypt(%s::text, %s) END "
        "WHERE id = %s",
        (
            name,
            norm.stripped,
            data.get("country", current["country"]),
            data.get("aliases", current["aliases"]),
            data.get("dob"),
            data.get("dob"),
            ctx.state.settings.data_key,
            subject_id,
        ),
    )
    ctx.audit("subject.updated", str(subject_id), {"fields": sorted(data)})
    return _own_subject(ctx, subject_id)


@subjects.post("/{subject_id}/monitor", summary="Turn continuous monitoring on or off")
def monitor_subject(
    subject_id: uuid.UUID, body: MonitorRequest, ctx: Ctx = Authorized(SCREEN)
) -> dict[str, Any]:
    _own_subject(ctx, subject_id)
    ctx.conn.execute(
        "UPDATE subjects SET monitored = %s WHERE id = %s", (body.monitored, subject_id)
    )
    ctx.audit("subject.monitoring", str(subject_id), {"monitored": body.monitored})
    created: list[dict[str, Any]] = []
    if body.monitored:  # check existing mentions and list entries right away
        current = _own_subject(ctx, subject_id)
        created = scan_subject(
            ctx.conn,
            ctx.tenant_id,
            current,
            ctx.state.settings.data_key,
            ctx.state.extras.get("embedder"),
        )
        for alert in created:
            ctx.publish(
                {
                    "type": "alert.created",
                    "alert_id": str(alert["id"]),
                    "severity": alert["severity"],
                    "subject": current["name"],
                }
            )
    return {"id": subject_id, "monitored": body.monitored, "alerts_created": len(created)}


@subjects.delete("/{subject_id}", status_code=204, summary="Soft delete a subject")
def delete_subject(subject_id: uuid.UUID, ctx: Ctx = Authorized(SCREEN)) -> Response:
    _own_subject(ctx, subject_id)
    ctx.conn.execute(
        "UPDATE subjects SET deleted_at = now(), monitored = false WHERE id = %s", (subject_id,)
    )
    ctx.audit("subject.deleted", str(subject_id))
    return Response(status_code=204)
