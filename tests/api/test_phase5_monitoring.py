"""Monitoring, alert lifecycle outputs, graph, cases, reports, and the end-to-end exit test."""

from __future__ import annotations

import asyncio
import datetime as dt
import threading
import time
import uuid
from typing import Any

import httpx
from api_data import populate
from atlas.api import graph_service, ingest, monitoring
from atlas.api import report as reportmod
from atlas.api.db import PLATFORM
from media_data import add_media
from test_api_realtime import _cookie_header, server  # noqa: F401  (fixture)

NOW = dt.datetime.now(dt.UTC)


def article(url: str = "https://news.example/fixture", text: str | None = None) -> ingest.ArticleIn:
    return ingest.ArticleIn(
        url=url,
        title="Fixture article",
        text=text or "Alpha Holdings was accused of fraud. Beta Trading denied any link.",
        domain="news.example",
        published_at=NOW - dt.timedelta(days=2),
    )


def mentions(severity: int = 4) -> list[ingest.MentionIn]:
    return [
        ingest.MentionIn(
            0,
            14,
            "Alpha Holdings",
            "Q1",
            0.9,
            0.92,
            findings=[ingest.FindingIn("fraud", severity, "Alpha Holdings was accused of fraud.")],
        ),
        ingest.MentionIn(37, 49, "Beta Trading", "Q2", 0.9, 0.9),
    ]


def ingest_fixture(worker: Any, **kwargs: Any) -> dict[str, Any]:
    with worker.db.session(PLATFORM) as conn:
        return ingest.ingest_article(conn, article(**kwargs), mentions())


def monitor(client: Any, name: str = "Alpha Holdings") -> str:
    created = client.post("/api/v1/subjects", json={"name": name, "kind": "organization"})
    sid = created.json()["id"]
    reply = client.post(f"/api/v1/subjects/{sid}/monitor", json={"monitored": True})
    assert reply.status_code == 200
    return str(sid)


# --- Ingestion and co-occurrence ---------------------------------------------------------------


def test_ingest_stores_snippet_only_and_cooccurrence(seed: dict[str, Any], worker: Any) -> None:
    populate(seed)
    result = ingest_fixture(
        worker, text="Alpha Holdings was accused of fraud. Beta Trading denied. " * 8
    )
    assert result["mentions"] == 2 and result["findings"] == 1
    snippet = seed["admin"].execute("SELECT snippet FROM articles").fetchone()[0]
    assert len(snippet) <= 300
    rows = (
        seed["admin"]
        .execute("SELECT entity_a, entity_b, sentences FROM entity_cooccurrence")
        .fetchall()
    )
    assert rows == []  # the two mentions are in different sentences


def test_cooccurrence_counts_shared_sentences() -> None:
    text = "Alpha met Beta. Alone here. Alpha and Beta again."
    spans = ingest.sentence_spans(text, "en")
    pairs = ingest.cooccurrence([(0, 1), (11, 2), (30, 1), (40, 2), (22, 3)], spans)
    assert pairs == {(1, 2): 2}
    assert ingest.cooccurrence([(0, None)], spans) == {}


def test_reingest_replaces_rather_than_duplicates(seed: dict[str, Any], worker: Any) -> None:
    populate(seed)
    ingest_fixture(worker)
    ingest_fixture(worker)
    db = seed["admin"]
    assert db.execute("SELECT count(*) FROM articles").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM mentions").fetchone()[0] == 2


# --- Monitoring --------------------------------------------------------------------------------


def test_monitoring_creates_one_alert_and_deduplicates(
    make_client: Any, seed: dict[str, Any], worker: Any
) -> None:
    populate(seed)
    client = make_client("a_analyst")
    monitor(client)
    ingest_fixture(worker)
    first = monitoring.scan_monitored(worker)
    assert first["alerts_created"] == 1 and first["subjects_scanned"] == 1
    again = monitoring.scan_monitored(worker, only_if_new=False)
    assert again["alerts_created"] == 0  # same subject, article, and entry
    alerts = client.get("/api/v1/alerts").json()["items"]
    created = [a for a in alerts if a["subject_name"] == "Alpha Holdings"]
    assert len(created) == 1 and created[0]["severity"] in {"medium", "high", "critical"}
    detail = client.get(f"/api/v1/alerts/{created[0]['id']}").json()
    assert detail["score_breakdown"]["components"]["adverse"]["points"] > 0
    assert "fraud" in detail["narrative"] and "lead for human review" in detail["narrative"]
    assert detail["events"][0]["event_type"] == "created"
    assert detail["article_url"] == "https://news.example/fixture"


def test_unchanged_data_skips_the_scan(make_client: Any, seed: dict[str, Any], worker: Any) -> None:
    populate(seed)
    monitor(make_client("a_analyst"))
    monitoring.scan_monitored(worker, only_if_new=False)
    skipped = monitoring.scan_monitored(worker)
    assert skipped["skipped_no_new_data"] is True and skipped["subjects_scanned"] == 0
    ingest_fixture(worker)
    assert monitoring.scan_monitored(worker)["skipped_no_new_data"] is False


def test_new_watchlist_entry_alerts_a_monitored_subject(
    make_client: Any, seed: dict[str, Any], worker: Any
) -> None:
    populate(seed)
    client = make_client("a_analyst")
    client.post(
        "/api/v1/subjects", json={"name": "Blue Harbour Trading Ltd", "kind": "organization"}
    )
    sid = client.get("/api/v1/subjects", params={"q": "blue harbour"}).json()["items"][0]["id"]
    client.post(f"/api/v1/subjects/{sid}/monitor", json={"monitored": True})
    assert monitoring.scan_monitored(worker, only_if_new=False)["alerts_created"] == 0
    from atlas.names import normalize_name

    norm = normalize_name("Blue Harbour Trading Limited", "organization")
    source = seed["admin"].execute("SELECT id FROM watchlist_sources").fetchone()[0]
    seed["admin"].execute(
        "INSERT INTO watchlist_entries (source_id, source_key, kind, name, name_norm, name_key, "
        "programs, countries) VALUES (%s, 'new1', 'organization', 'Blue Harbour Trading Limited', "
        "%s, %s, %s, %s)",
        (source, norm.stripped, norm.key, ["SDGZ"], ["PA"]),
    )
    result = monitoring.scan_monitored(worker)
    assert result["alerts_created"] == 1
    alert = next(
        a
        for a in client.get("/api/v1/alerts").json()["items"]
        if a["subject_name"] == "Blue Harbour Trading Ltd"
    )
    detail = client.get(f"/api/v1/alerts/{alert['id']}").json()
    assert (
        detail["article_id"] is None and detail["watchlist_name"] == "Blue Harbour Trading Limited"
    )
    assert monitoring.scan_monitored(worker, only_if_new=False)["alerts_created"] == 0


def test_low_scoring_evidence_does_not_alert(
    make_client: Any, seed: dict[str, Any], worker: Any
) -> None:
    populate(seed)
    monitor(make_client("a_analyst"))
    with worker.db.session(PLATFORM) as conn:
        ingest.ingest_article(
            conn,
            article("https://news.example/old"),
            [
                ingest.MentionIn(
                    0,
                    14,
                    "Alpha Holdings",
                    "Q1",
                    0.9,
                    0.2,
                    findings=[ingest.FindingIn("other", 1, "Minor note.")],
                )
            ],
        )
        conn.execute("UPDATE articles SET published_at = now() - interval '4000 days'")
    assert monitoring.scan_monitored(worker)["alerts_created"] == 0


def test_toggling_monitoring_scans_immediately(
    make_client: Any, seed: dict[str, Any], worker: Any
) -> None:
    ids = populate(seed)
    add_media(seed, ids)
    client = make_client("a_analyst")
    sid = client.post(
        "/api/v1/subjects", json={"name": "Alpha Holdings", "kind": "organization"}
    ).json()["id"]
    reply = client.post(f"/api/v1/subjects/{sid}/monitor", json={"monitored": True}).json()
    assert reply["alerts_created"] == 1
    off = client.post(f"/api/v1/subjects/{sid}/monitor", json={"monitored": False}).json()
    assert off["alerts_created"] == 0


def test_monitoring_is_tenant_scoped(make_client: Any, seed: dict[str, Any], worker: Any) -> None:
    populate(seed)
    monitor(make_client("a_analyst"))
    monitor(make_client("b_analyst"))
    ingest_fixture(worker)
    assert monitoring.scan_monitored(worker)["alerts_created"] == 2
    a = make_client("a_analyst").get("/api/v1/alerts").json()["items"]
    b = make_client("b_analyst").get("/api/v1/alerts").json()["items"]
    assert {x["id"] for x in a}.isdisjoint({x["id"] for x in b})


def test_scan_publishes_events_to_the_owning_tenant(
    make_client: Any, seed: dict[str, Any], worker: Any
) -> None:
    populate(seed)
    monitor(make_client("a_analyst"))
    ingest_fixture(worker)
    got: dict[str, list[str]] = {"A": [], "B": []}

    async def listen(name: str, tenant: Any) -> None:
        stream = worker.bus.subscribe(str(tenant))
        try:
            got[name].append(await asyncio.wait_for(stream.__anext__(), 1.0))
        except TimeoutError:
            pass
        await stream.aclose()

    async def run() -> None:
        tasks = [
            asyncio.ensure_future(listen("A", seed["tenants"]["A"])),
            asyncio.ensure_future(listen("B", seed["tenants"]["B"])),
        ]
        await asyncio.sleep(0.2)
        await asyncio.to_thread(monitoring.scan_monitored, worker)
        await asyncio.gather(*tasks)

    asyncio.run(run())
    assert got["A"] and "alert.created" in got["A"][0] and got["B"] == []


def test_alert_statistics_job_and_feedback(
    make_client: Any, seed: dict[str, Any], worker: Any
) -> None:
    populate(seed)
    client = make_client("a_analyst")
    monitor(client)
    ingest_fixture(worker)
    monitoring.scan_monitored(worker)
    alert = next(
        a
        for a in client.get("/api/v1/alerts").json()["items"]
        if a["subject_name"] == "Alpha Holdings"
    )
    path = f"/api/v1/alerts/{alert['id']}"
    assert client.patch(path, json={"status": "closed"}).status_code == 400  # reason is mandatory
    assert (
        client.patch(
            path, json={"status": "true_match", "reason": "Confirmed by filing"}
        ).status_code
        == 200
    )
    feedback = (
        seed["admin"]
        .execute("SELECT label, score, features, reviewer_id FROM alert_feedback")
        .fetchall()
    )
    assert len(feedback) == 1 and feedback[0][0] == "true_match" and feedback[0][1] > 0
    assert "components" in feedback[0][2] and feedback[0][3] == seed["users"]["a_analyst"]
    assert monitoring.refresh_alert_stats(worker) == 2
    row = (
        seed["admin"]
        .execute(
            "SELECT alerts, confirmed_true, false_positive FROM alert_stats_daily "
            "WHERE day = current_date"
        )
        .fetchone()
    )
    assert row[0] >= 2 and row[1] == 1 and row[2] == 0
    # Reopening and deciding again adds a second labeled record rather than overwriting.
    client.patch(path, json={"status": "in_review"})
    client.patch(path, json={"status": "false_positive", "reason": "Different company"})
    labels = [
        r[0]
        for r in seed["admin"].execute("SELECT label FROM alert_feedback ORDER BY id").fetchall()
    ]
    assert labels == ["true_match", "false_positive"]


# --- Graph -------------------------------------------------------------------------------------


def seed_cooccurrence(seed: dict[str, Any], ids: dict[str, Any], articles: int) -> None:
    a, b, c = ids["entities"]
    db = seed["admin"]
    for i in range(articles):
        art = db.execute(
            "INSERT INTO articles (url, title) VALUES (%s, 't') RETURNING id", (f"https://x/{i}",)
        ).fetchone()[0]
        db.execute(
            "INSERT INTO entity_cooccurrence VALUES (%s, %s, %s, %s)",
            (min(a, c), max(a, c), art, 2),
        )


def test_graph_merges_relationship_and_cooccurrence_edges(
    make_client: Any, seed: dict[str, Any]
) -> None:
    ids = populate(seed)
    a, b, c = ids["entities"]
    seed_cooccurrence(seed, ids, 2)  # a and c never share a registry edge
    client = make_client("a_viewer")
    near = client.get("/api/v1/graph/neighbors", params={"entity_id": a, "depth": 1}).json()
    kinds = {(e["kind"], tuple(sorted((e["source_id"], e["target_id"])))) for e in near["edges"]}
    assert ("relationship", tuple(sorted((a, b)))) in kinds
    co = next(e for e in near["edges"] if e["kind"] == "cooccurrence")
    assert co["weight"] == 4 and co["article_count"] == 2 and len(co["articles"]) == 2
    assert co["source"] == "articles"
    rel = next(e for e in near["edges"] if e["kind"] == "relationship")
    assert rel["source"] == "test" and rel["rel_type"] == "owns"
    assert {n["id"] for n in near["nodes"]} == {a, b, c}
    short = client.get("/api/v1/graph/path", params={"src": a, "dst": c}).json()
    assert short["found"] and short["hops"] == 1 and short["edges"][0]["kinds"] == ["cooccurrence"]
    longer = client.get("/api/v1/graph/path", params={"src": b, "dst": c, "max_depth": 1}).json()
    assert longer["found"] and longer["hops"] == 1


def test_graph_node_cap_and_depth(make_client: Any, seed: dict[str, Any], monkeypatch: Any) -> None:
    ids = populate(seed)
    a, b, c = ids["entities"]
    client = make_client("a_viewer")
    d1 = client.get("/api/v1/graph/neighbors", params={"entity_id": a, "depth": 1}).json()
    assert {n["id"] for n in d1["nodes"]} == {a, b} and d1["truncated"] is False
    d3 = client.get("/api/v1/graph/neighbors", params={"entity_id": a, "depth": 3}).json()
    assert {n["id"] for n in d3["nodes"]} == {a, b, c}
    assert (
        client.get("/api/v1/graph/neighbors", params={"entity_id": a, "depth": 4}).status_code
        == 422
    )
    monkeypatch.setattr(graph_service, "NODE_CAP", 2)
    capped = client.get("/api/v1/graph/neighbors", params={"entity_id": a, "depth": 3}).json()
    assert len(capped["nodes"]) == 2 and capped["truncated"] is True
    nopath = client.get("/api/v1/graph/path", params={"src": a, "dst": 999999}).json()
    assert nopath["found"] is False


def test_cases_group_alerts_and_entities(make_client: Any, seed: dict[str, Any]) -> None:
    ids = populate(seed)
    client = make_client("a_analyst")
    case = client.post(
        "/api/v1/cases",
        json={"title": "Network review", "alert_ids": [str(ids["A"]["alert"])]},
    ).json()
    path = f"/api/v1/cases/{case['id']}"
    updated = client.patch(
        path,
        json={
            "add_entity_ids": ids["entities"][:2],
            "note": "Ownership chain to verify",
            "due_date": "2026-12-31",
        },
    ).json()
    assert [e["qid"] for e in updated["entities"]] == ["Q1", "Q2"] or len(updated["entities"]) == 2
    assert updated["due_date"] == "2026-12-31" and len(updated["alerts"]) == 1
    removed = client.patch(path, json={"remove_entity_ids": [ids["entities"][0]]}).json()
    assert len(removed["entities"]) == 1
    assert client.patch(path, json={"add_entity_ids": [999999]}).status_code == 404


# --- Reports -----------------------------------------------------------------------------------


def test_report_html_contains_required_sections() -> None:
    data = {
        "screening": {
            "id": "sid",
            "created_at": "2026-11-17",
            "config_version": "risk-config-v2.0",
            "risk_score": 72.5,
            "risk_level": "high",
            "score_breakdown": {
                "thresholds": {"medium": 40, "high": 60, "critical": 80},
                "components": {
                    "list": {
                        "weight": 0.4,
                        "list_weight": 1.0,
                        "match_confidence": 0.9,
                        "points": 36.0,
                    }
                },
            },
        },
        "subject": {
            "name": "Acme <b>Corp</b>",
            "kind": "organization",
            "country": "GB",
            "monitored": True,
        },
        "matches": [
            {
                "match_type": "watchlist",
                "confidence": 0.9,
                "source": "ofac_sdn",
                "features": {
                    "name": "ACME CORP",
                    "via": "name",
                    "country": "match",
                    "birth_year": "unknown",
                },
            }
        ],
        "evidence": [
            {
                "url": "https://news.example/a",
                "title": "Title",
                "published_at": "2026-11-01",
                "evidence": "Acme was accused.",
                "category": "fraud",
                "severity": 4,
                "confidence": 0.8,
            },
            {
                "url": "javascript:alert(1)",
                "title": "x",
                "published_at": None,
                "evidence": "e",
                "category": "c",
                "severity": 1,
                "confidence": 0.1,
            },
        ],
        "decisions": [
            {
                "alert_id": "a1",
                "status": "false_positive",
                "reason": "Different company",
                "reviewer": "analyst@example.org",
                "at": "2026-11-02",
            }
        ],
    }
    rid = uuid.uuid4()
    page = reportmod.render_html(data, rid, NOW)
    for needle in (
        str(rid),
        "Score breakdown",
        "Different company",
        "analyst@example.org",
        "leads for human review",
        'href="https://news.example/a"',
        "ofac_sdn",
        NOW.isoformat(),
    ):
        assert needle in page, needle
    assert "<b>Corp</b>" not in page and "&lt;b&gt;" in page  # text is escaped
    assert 'href="javascript' not in page  # only http and https become links


def test_pdf_report_endpoint(make_client: Any, seed: dict[str, Any]) -> None:
    ids = populate(seed)
    add_media(seed, ids)
    client = make_client("a_analyst")
    screening = client.post(
        "/api/v1/screenings",
        json={"subject": {"name": "Viktor Petrovich Example", "kind": "person"}},
    ).json()
    reply = client.get(f"/api/v1/screenings/{screening['id']}/report.pdf")
    assert reply.status_code == 200 and reply.content.startswith(b"%PDF")
    assert reply.headers["content-type"] == "application/pdf" and len(reply.content) > 2000
    rid = reply.headers["x-report-id"]
    row = (
        seed["admin"]
        .execute("SELECT screening_id, sha256 FROM reports WHERE id = %s", (rid,))
        .fetchone()
    )
    import hashlib

    assert str(row[0]) == screening["id"] and row[1] == hashlib.sha256(reply.content).hexdigest()
    assert (
        seed["admin"]
        .execute("SELECT count(*) FROM audit_log WHERE action = 'report.generated'")
        .fetchone()[0]
        == 1
    )
    assert (
        make_client("b_analyst").get(f"/api/v1/screenings/{screening['id']}/report.pdf").status_code
        == 404
    )
    assert make_client().get(f"/api/v1/screenings/{screening['id']}/report.pdf").status_code == 401
    assert (
        make_client("finance").get(f"/api/v1/screenings/{screening['id']}/report.pdf").status_code
        == 403
    )


# --- End to end (phase 5 exit test) ------------------------------------------------------------


def test_end_to_end_subject_to_report(
    server: str,  # noqa: F811
    make_client: Any,
    seed: dict[str, Any],
    worker: Any,
) -> None:
    """Create a subject, monitor it, ingest a fixture article, receive the alert over SSE,
    resolve it, and export the report."""
    populate(seed)
    analyst = make_client("a_analyst")
    subject_id = monitor(analyst)  # create subject and turn monitoring on

    received: list[str] = []
    ready = threading.Event()

    def listen() -> None:
        try:
            with httpx.stream(
                "GET", f"{server}/api/v1/stream/alerts", headers=_cookie_header(analyst), timeout=8
            ) as reply:
                ready.set()
                for line in reply.iter_lines():
                    received.append(line)
                    if line.startswith("data:"):
                        return
        except httpx.ReadTimeout:
            return

    thread = threading.Thread(target=listen, daemon=True)
    thread.start()
    assert ready.wait(5)
    time.sleep(0.3)

    ingest_fixture(worker)  # the fixture article
    assert monitoring.scan_monitored(worker)["alerts_created"] == 1
    thread.join(timeout=6)
    event = next(line for line in received if line.startswith("data:"))
    assert "alert.created" in event and "Alpha Holdings" in event

    alert_id = next(
        a["id"]
        for a in analyst.get("/api/v1/alerts").json()["items"]
        if a["subject_name"] == "Alpha Holdings"
    )
    resolved = analyst.patch(f"/api/v1/alerts/{alert_id}", json={"status": "in_review"})
    assert resolved.status_code == 200
    done = analyst.patch(
        f"/api/v1/alerts/{alert_id}",
        json={"status": "false_positive", "reason": "Different company with a similar name"},
    )
    assert done.status_code == 200 and done.json()["resolved_at"] is not None

    screening = analyst.post("/api/v1/screenings", json={"subject_id": subject_id}).json()
    report = analyst.get(f"/api/v1/screenings/{screening['id']}/report.pdf")
    assert report.status_code == 200 and report.content.startswith(b"%PDF")
    stored = seed["admin"].execute("SELECT count(*) FROM reports").fetchone()[0]
    assert stored == 1
    actions = [
        r[0] for r in seed["admin"].execute("SELECT action FROM audit_log ORDER BY id").fetchall()
    ]
    assert {"subject.monitoring", "alert.updated", "report.generated"} <= set(actions)
