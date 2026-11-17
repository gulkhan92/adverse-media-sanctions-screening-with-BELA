"""Screening, subjects, alerts, cases, knowledge endpoints, and live events."""

from __future__ import annotations

import asyncio
import threading
from typing import Any

from api_data import populate

SCREEN = "/api/v1/screenings"


def test_screening_finds_watchlist_match(make_client: Any, seed: dict[str, Any]) -> None:
    populate(seed)
    client = make_client("a_analyst")
    reply = client.post(
        SCREEN,
        json={
            "subject": {
                "name": "Petrovich Viktor Example",
                "kind": "person",
                "country": "ru",
                "dob": "1970-05-01",
            }
        },
    )
    assert reply.status_code == 201, reply.text
    body = reply.json()
    assert "leads for human review" in body["disclaimer"]
    top = body["matches"][0]
    assert top["name"] == "Viktor Petrovich Example" and top["source"] == "ofac_sdn"
    assert top["features"]["country"] == "match" and top["features"]["birth_year"] == "match"
    assert body["risk_level"] in {"high", "critical"}
    stored = client.get(f"{SCREEN}/{body['id']}").json()
    assert stored["matches"][0]["watchlist_name"] == "Viktor Petrovich Example"


def test_conflicting_attributes_lower_the_score(make_client: Any, seed: dict[str, Any]) -> None:
    populate(seed)
    client = make_client("a_analyst")
    agree = client.post(
        SCREEN,
        json={"subject": {"name": "Viktor Petrovich Example", "kind": "person", "dob": "1970"}},
    ).json()
    clash = client.post(
        SCREEN,
        json={"subject": {"name": "Viktor Petrovich Example", "kind": "person", "dob": "1999"}},
    ).json()
    assert clash["risk_score"] < agree["risk_score"]


def test_no_match_is_low_risk(make_client: Any, seed: dict[str, Any]) -> None:
    populate(seed)
    reply = make_client("a_analyst").post(
        SCREEN, json={"subject": {"name": "Completely Unrelated Person", "kind": "person"}}
    )
    assert reply.json()["risk_level"] == "low" and reply.json()["matches"] == []


def test_subject_dob_is_encrypted_at_rest(make_client: Any, seed: dict[str, Any]) -> None:
    client = make_client("a_analyst")
    created = client.post(
        "/api/v1/subjects",
        json={"name": "Jane Roe", "kind": "person", "country": "GB", "dob": "1980-02-03"},
    ).json()
    raw = (
        seed["admin"]
        .execute("SELECT dob_enc FROM subjects WHERE id = %s", (created["id"],))
        .fetchone()[0]
    )
    assert b"1980" not in bytes(raw)
    assert client.get(f"/api/v1/subjects/{created['id']}").json()["dob"] == "1980-02-03"
    # Listings do not expose the identifier.
    assert "dob" not in client.get("/api/v1/subjects").json()["items"][0]


def test_subject_update_monitor_and_delete(make_client: Any, seed: dict[str, Any]) -> None:
    client = make_client("a_analyst")
    sid = client.post("/api/v1/subjects", json={"name": "Old Name", "kind": "person"}).json()["id"]
    assert (
        client.patch(f"/api/v1/subjects/{sid}", json={"name": "New Name"}).json()["name"]
        == "New Name"
    )
    assert client.post(f"/api/v1/subjects/{sid}/monitor", json={"monitored": True}).json()[
        "monitored"
    ]
    assert client.delete(f"/api/v1/subjects/{sid}").status_code == 204
    assert client.get(f"/api/v1/subjects/{sid}").status_code == 404
    assert client.patch(f"/api/v1/subjects/{sid}", json={}).status_code in (400, 404)


def test_validation_rejects_bad_input(make_client: Any) -> None:
    client = make_client("a_analyst")
    bad = [
        {"name": "", "kind": "person"},
        {"name": "x" * 400, "kind": "person"},
        {"name": "A B", "kind": "vessel"},
        {"name": "A B", "kind": "person", "dob": "yesterday"},
        {"name": "A B", "kind": "person", "unexpected": 1},
    ]
    for payload in bad:
        assert client.post("/api/v1/subjects", json=payload).status_code == 422, payload
    assert client.post(SCREEN, json={}).status_code == 422


def test_batch_and_idempotency(make_client: Any, seed: dict[str, Any]) -> None:
    populate(seed)
    client = make_client("a_analyst")
    body = {
        "subjects": [
            {"name": "Viktor Petrovich Example", "kind": "person"},
            {"name": "Northern Star Shipping", "kind": "organization"},
            {"name": "Nobody Special", "kind": "person"},
        ]
    }
    headers = {"Idempotency-Key": "batch-key-0001"}
    first = client.post(f"{SCREEN}/batch", json=body, headers=headers)
    assert first.status_code == 201 and first.json()["total"] == 3
    replay = client.post(f"{SCREEN}/batch", json=body, headers=headers)
    assert replay.headers["idempotent-replay"] == "true" and replay.json() == first.json()
    count = seed["admin"].execute("SELECT count(*) FROM screening_batches").fetchone()[0]
    assert count == 1
    other = client.post(
        f"{SCREEN}/batch",
        json={"subjects": [{"name": "Different", "kind": "person"}]},
        headers=headers,
    )
    assert other.status_code == 422
    assert client.post(f"{SCREEN}/batch", json=body).status_code == 422  # key is required
    status = client.get(f"{SCREEN}/batch/{first.json()['id']}").json()
    assert status["status"] == "completed" and len(status["results"]) == 3
    usage = seed["admin"].execute("SELECT batch_rows FROM usage_daily").fetchone()[0]
    assert usage == 3


def test_batch_limits(make_client: Any) -> None:
    client = make_client("a_analyst")
    too_many = {"subjects": [{"name": f"Person {i}", "kind": "person"} for i in range(1001)]}
    reply = client.post(f"{SCREEN}/batch", json=too_many, headers={"Idempotency-Key": "k" * 12})
    assert reply.status_code == 422


def test_text_screening_reports_missing_service(make_client: Any, seed: dict[str, Any]) -> None:
    seed["state"].settings.nel_url = "http://127.0.0.1:9"  # nothing listens here
    reply = make_client("a_analyst").post(
        f"{SCREEN}/text", json={"text": "Viktor Petrovich Example visited the port."}
    )
    assert reply.status_code == 503 and reply.json()["type"].endswith("nel-unavailable")


# --- Alerts and cases --------------------------------------------------------------------------


def test_alert_review_flow(make_client: Any, seed: dict[str, Any]) -> None:
    ids = populate(seed)
    alert = ids["A"]["alert"]
    client = make_client("a_analyst")
    path = f"/api/v1/alerts/{alert}"
    assert client.patch(path, json={"status": "in_review"}).json()["status"] == "in_review"
    assert client.patch(path, json={"status": "false_positive"}).status_code == 400  # needs reason
    done = client.patch(
        path, json={"status": "false_positive", "reason": "Different date of birth"}
    )
    assert done.status_code == 200 and done.json()["resolved_at"] is not None
    assert client.patch(path, json={"status": "new"}).status_code == 400  # invalid transition
    comment = client.post(f"{path}/comments", json={"body": "Checked passport copy"})
    assert comment.status_code == 201
    detail = client.get(path).json()
    kinds = [e["event_type"] for e in detail["events"]]
    assert kinds == ["status_change", "status_change", "comment"]
    assert detail["events"][1]["reason"] == "Different date of birth"
    assert "disclaimer" in detail
    actions = {r[0] for r in seed["admin"].execute("SELECT action FROM audit_log").fetchall()}
    assert {"alert.updated", "alert.commented"} <= actions


def test_alert_assignment_and_filters(make_client: Any, seed: dict[str, Any]) -> None:
    ids = populate(seed)
    client = make_client("a_manager")
    path = f"/api/v1/alerts/{ids['A']['alert']}"
    assigned = client.patch(path, json={"assignee_id": str(seed["users"]["a_analyst"])})
    assert assigned.json()["assignee_id"] == str(seed["users"]["a_analyst"])
    found = client.get("/api/v1/alerts", params={"severity": "high", "status": "new"}).json()
    assert len(found["items"]) == 1
    assert client.get("/api/v1/alerts", params={"status": "bogus"}).status_code == 422


def test_pagination_cursor(make_client: Any, seed: dict[str, Any]) -> None:
    ids = populate(seed)
    for i in range(4):
        article = (
            seed["admin"]
            .execute(
                "INSERT INTO articles (url, title) VALUES (%s, 't') RETURNING id",
                (f"https://x/{i}",),
            )
            .fetchone()[0]
        )
        seed["admin"].execute(
            "INSERT INTO alerts (tenant_id, subject_id, severity, article_id) "
            "VALUES (%s, %s, 'low', %s)",
            (seed["tenants"]["A"], ids["A"]["subject"], article),
        )
    client = make_client("a_viewer")
    seen: list[str] = []
    cursor = None
    for _ in range(5):
        page = client.get("/api/v1/alerts", params={"limit": 2, "cursor": cursor}).json()
        seen += [row["id"] for row in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(seen) == len(set(seen)) == 5
    assert client.get("/api/v1/alerts", params={"cursor": "not-a-cursor"}).status_code == 400


def test_case_management(make_client: Any, seed: dict[str, Any]) -> None:
    ids = populate(seed)
    client = make_client("a_analyst")
    created = client.post(
        "/api/v1/cases",
        json={"title": "Review of Subject A", "alert_ids": [str(ids["A"]["alert"])]},
    )
    assert created.status_code == 201 and len(created.json()["alerts"]) == 1
    case_id = created.json()["id"]
    patched = client.patch(
        f"/api/v1/cases/{case_id}", json={"status": "in_progress", "note": "Requested documents"}
    ).json()
    assert (
        patched["status"] == "in_progress" and patched["notes"][0]["body"] == "Requested documents"
    )
    assert client.patch(f"/api/v1/cases/{case_id}", json={"status": "invalid"}).status_code == 422
    owner = client.patch(
        f"/api/v1/cases/{case_id}", json={"owner_id": str(seed["users"]["b_admin"])}
    )
    assert owner.status_code == 400


def test_event_bus_is_tenant_scoped(seed: dict[str, Any]) -> None:
    bus = seed["state"].bus
    received: dict[str, list[str]] = {"A": [], "B": []}

    async def listen(name: str, tenant: Any, ready: threading.Event) -> None:
        stream = bus.subscribe(str(tenant))
        task = asyncio.ensure_future(stream.__anext__())
        ready.set()
        try:
            received[name].append(await asyncio.wait_for(task, 0.5))
        except TimeoutError:
            pass
        await stream.aclose()

    async def run() -> None:
        ready_a, ready_b = threading.Event(), threading.Event()
        tasks = [
            asyncio.ensure_future(listen("A", seed["tenants"]["A"], ready_a)),
            asyncio.ensure_future(listen("B", seed["tenants"]["B"], ready_b)),
        ]
        await asyncio.sleep(0.1)
        bus.publish(str(seed["tenants"]["A"]), {"type": "alert.updated"})
        await asyncio.gather(*tasks)

    asyncio.run(run())
    assert received["A"] and "alert.updated" in received["A"][0]
    assert received["B"] == []


def test_stream_requires_authentication(make_client: Any) -> None:
    assert make_client().get("/api/v1/stream/alerts").status_code == 401
    assert make_client("finance").get("/api/v1/stream/alerts").status_code == 403


# --- Knowledge ---------------------------------------------------------------------------------


def test_knowledge_endpoints(make_client: Any, seed: dict[str, Any]) -> None:
    ids = populate(seed)
    client = make_client("a_viewer")
    found = client.get("/api/v1/entities/search", params={"q": "alpha holdings"}).json()["items"]
    assert found and found[0]["qid"] == "Q1"
    detail = client.get(f"/api/v1/entities/{ids['entities'][0]}").json()
    assert detail["relationship_count"] == 1
    near = client.get(
        "/api/v1/graph/neighbors", params={"entity_id": ids["entities"][0], "depth": 2}
    )
    assert len(near.json()["nodes"]) == 3
    path = client.get(
        "/api/v1/graph/path", params={"src": ids["entities"][0], "dst": ids["entities"][2]}
    ).json()
    assert path["found"] and len(path["edges"]) == 2
    assert client.get("/api/v1/graph/path", params={"src": 1, "dst": 1}).status_code == 400
    sources = client.get("/api/v1/watchlists/sources").json()["items"]
    assert sources[0]["code"] == "ofac_sdn"
    entries = client.get("/api/v1/watchlists/entries", params={"limit": 1, "q": "viktor"}).json()
    assert entries["items"][0]["name"].startswith("Viktor")
    assert client.get("/api/v1/entities/999999").status_code == 404


def test_analytics_and_revenue_labels(make_client: Any, seed: dict[str, Any]) -> None:
    populate(seed)
    overview = make_client("a_viewer").get("/api/v1/analytics/overview").json()
    assert overview["alerts_by_severity"][0]["n"] == 1
    summary = make_client("finance").get("/api/v1/platform/revenue/summary").json()
    assert summary["demo_data"] is True and "Demo data" in summary["label"]
    assert make_client("a_admin").get("/api/v1/billing/plan").json()["demo_data"] is True
