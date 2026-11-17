"""CSV batch screening: template, validation, asynchronous processing, and export.

Uploaded rows are stored encrypted until processed and are wiped afterwards. A row that fails
validation or processing is recorded with its error and does not stop the batch.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import uuid
from typing import Any

import psycopg
from pydantic import ValidationError

from atlas.api.db import Row, Scope
from atlas.api.schemas import SubjectIn
from atlas.api.screening import find_or_create_subject, meter_usage, run_screening
from atlas.api.state import AppState

log = logging.getLogger("atlas.api.batch")
MAX_ROWS = 10_000
CHUNK = 50
COLUMNS = ["name", "kind", "country", "dob", "identifier_type", "identifier_value", "aliases"]
REQUIRED = ("name", "kind")
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def template_csv() -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(COLUMNS)
    writer.writerow(["Example Trading Company Ltd", "organization", "GB", "", "", "", ""])
    writer.writerow(
        ["Example Person", "person", "US", "1980-01-31", "passport", "X1234567", "E. Person"]
    )
    return out.getvalue()


def safe_cell(value: Any) -> str:
    """Neutralise spreadsheet formulas in exported text."""
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(FORMULA_PREFIXES) else text


def _row_to_subject(raw: dict[str, str]) -> SubjectIn:
    identifiers = []
    if (raw.get("identifier_value") or "").strip():
        identifiers.append(
            {"type": (raw.get("identifier_type") or "id").strip(), "value": raw["identifier_value"]}
        )
    aliases = [a.strip() for a in (raw.get("aliases") or "").split(";") if a.strip()]
    return SubjectIn.model_validate(
        {
            "name": raw.get("name", ""),
            "kind": (raw.get("kind") or "").strip().lower(),
            "country": (raw.get("country") or "").strip() or None,
            "dob": (raw.get("dob") or "").strip() or None,
            "aliases": aliases,
            "identifiers": identifiers,
        }
    )


class CsvError(ValueError):
    pass


def parse_csv(content: bytes) -> tuple[list[tuple[int, dict[str, Any]]], list[tuple[int, str]]]:
    """Return (valid rows as (row number, subject data), rejected rows as (row number, reason))."""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise CsvError("The file must be UTF-8 encoded.") from None
    reader = csv.DictReader(io.StringIO(text))
    header = [h.strip().lower() for h in (reader.fieldnames or [])]
    missing = [c for c in REQUIRED if c not in header]
    if missing:
        raise CsvError(f"Missing required column(s): {', '.join(missing)}. Use the template.")
    reader.fieldnames = header
    valid: list[tuple[int, dict[str, Any]]] = []
    rejected: list[tuple[int, str]] = []
    for number, raw in enumerate(reader, start=1):
        if number > MAX_ROWS:
            raise CsvError(f"The file has more than {MAX_ROWS} rows.")
        if not any((v or "").strip() for v in raw.values() if isinstance(v, str)):
            continue
        try:
            valid.append((number, _row_to_subject(raw).model_dump()))
        except ValidationError as error:
            first = error.errors()[0]
            field = ".".join(str(p) for p in first["loc"])
            rejected.append((number, f"{field}: {first['msg']}"))
    if not valid and not rejected:
        raise CsvError("The file has no data rows.")
    return valid, rejected


def create_batch(
    conn: psycopg.Connection[Row],
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
    filename: str,
    valid: list[tuple[int, dict[str, Any]]],
    rejected: list[tuple[int, str]],
    key: str,
) -> uuid.UUID:
    batch = conn.execute(
        "INSERT INTO screening_batches (tenant_id, status, total, requested_by, source, filename, "
        "failed) VALUES (%s, 'queued', %s, %s, 'csv', %s, %s) RETURNING id",
        (tenant_id, len(valid) + len(rejected), user_id, filename[:200], len(rejected)),
    ).fetchone()
    assert batch is not None
    for number, payload in valid:
        conn.execute(
            "INSERT INTO batch_rows (batch_id, row_no, payload_enc) "
            "VALUES (%s, %s, pgp_sym_encrypt(%s::text, %s))",
            (batch["id"], number, json.dumps(payload), key),
        )
    for number, reason in rejected:
        conn.execute(
            "INSERT INTO batch_rows (batch_id, row_no, status, error) "
            "VALUES (%s, %s, 'failed', %s)",
            (batch["id"], number, reason),
        )
    return batch["id"]  # type: ignore[no-any-return]


def process_batch(state: AppState, tenant_id: uuid.UUID, batch_id: uuid.UUID) -> None:
    """Screen every pending row. Safe to call again: only pending rows are processed."""
    key = state.settings.data_key
    embedder = state.extras.get("embedder")
    scope = Scope(tenant_id=tenant_id)
    with state.db.session(scope) as conn:
        info = conn.execute(
            "UPDATE screening_batches SET status = 'running' WHERE id = %s RETURNING requested_by",
            (batch_id,),
        ).fetchone()
    if info is None:
        return
    requested_by = info["requested_by"]
    while True:
        with state.db.session(scope) as conn:
            rows = conn.execute(
                "SELECT id, row_no, pgp_sym_decrypt(payload_enc, %s) AS payload FROM batch_rows "
                "WHERE batch_id = %s AND status = 'pending' ORDER BY row_no LIMIT %s",
                (key, batch_id, CHUNK),
            ).fetchall()
            if not rows:
                break
            done = failed = 0
            for row in rows:
                try:
                    with conn.transaction():  # a failing row rolls back only itself
                        data = json.loads(row["payload"])
                        subject = find_or_create_subject(conn, tenant_id, data, key)
                        result = run_screening(
                            conn,
                            tenant_id,
                            subject,
                            data.get("dob"),
                            requested_by,
                            batch_id,
                            data.get("identifiers") or None,
                            embedder,
                        )
                        conn.execute(
                            "UPDATE batch_rows SET status = 'done', screening_id = %s, "
                            "payload_enc = NULL WHERE id = %s",
                            (result["id"], row["id"]),
                        )
                    done += 1
                except Exception as error:  # noqa: BLE001 - recorded per row
                    log.warning("batch row failed batch=%s row=%s", batch_id, row["row_no"])
                    conn.execute(
                        "UPDATE batch_rows SET status = 'failed', payload_enc = NULL, error = %s "
                        "WHERE id = %s",
                        (f"processing error: {type(error).__name__}", row["id"]),
                    )
                    failed += 1
            conn.execute(
                "UPDATE screening_batches SET completed = completed + %s, failed = failed + %s "
                "WHERE id = %s",
                (done, failed, batch_id),
            )
            meter_usage(conn, tenant_id, 0, done)
    with state.db.session(scope) as conn:
        conn.execute(
            "UPDATE screening_batches SET finished_at = now(), status = CASE WHEN completed = 0 "
            "AND total > 0 THEN 'failed' ELSE 'completed' END WHERE id = %s",
            (batch_id,),
        )


def batch_status(conn: psycopg.Connection[Row], batch_id: uuid.UUID) -> Row | None:
    row = conn.execute(
        "SELECT id, status, source, filename, total, completed, failed, created_at, finished_at "
        "FROM screening_batches WHERE id = %s",
        (batch_id,),
    ).fetchone()
    if row is None:
        return None
    done = row["completed"] + row["failed"]
    row["progress"] = round(done / row["total"], 4) if row["total"] else 1.0
    row["errors"] = conn.execute(
        "SELECT row_no, error FROM batch_rows WHERE batch_id = %s AND status = 'failed' "
        "ORDER BY row_no LIMIT 200",
        (batch_id,),
    ).fetchall()
    return row


def export_csv(conn: psycopg.Connection[Row], batch_id: uuid.UUID) -> str:
    rows = conn.execute(
        "SELECT r.row_no, r.status, r.error, s.risk_score, s.risk_level, sub.name, sub.kind, "
        "sub.country, (SELECT m.features ->> 'name' FROM screening_matches m "
        " WHERE m.screening_id = s.id ORDER BY m.confidence DESC LIMIT 1) AS top_match, "
        "(SELECT max(m.confidence) FROM screening_matches m WHERE m.screening_id = s.id) AS conf "
        "FROM batch_rows r LEFT JOIN screenings s ON s.id = r.screening_id "
        "LEFT JOIN subjects sub ON sub.id = s.subject_id WHERE r.batch_id = %s ORDER BY r.row_no",
        (batch_id,),
    ).fetchall()
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(
        [
            "row",
            "status",
            "name",
            "kind",
            "country",
            "risk_score",
            "risk_level",
            "top_match",
            "top_confidence",
            "error",
        ]
    )
    for r in rows:
        writer.writerow(
            [
                r["row_no"],
                r["status"],
                safe_cell(r["name"]),
                r["kind"] or "",
                r["country"] or "",
                r["risk_score"] if r["risk_score"] is not None else "",
                r["risk_level"] or "",
                safe_cell(r["top_match"]),
                r["conf"] if r["conf"] is not None else "",
                safe_cell(r["error"]),
            ]
        )
    return out.getvalue()
