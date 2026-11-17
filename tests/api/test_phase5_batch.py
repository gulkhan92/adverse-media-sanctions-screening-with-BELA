"""CSV batch upload: template, validation, async processing, partial failure, export."""

from __future__ import annotations

import io
from typing import Any

from api_data import populate

UPLOAD = "/api/v1/screenings/batch/upload"
HEADER = "name,kind,country,dob,identifier_type,identifier_value,aliases\n"


def upload(client: Any, text: str | bytes, name: str = "subjects.csv") -> Any:
    data = text.encode() if isinstance(text, str) else text
    return client.post(UPLOAD, files={"file": (name, io.BytesIO(data), "text/csv")})


def test_template_is_valid_input(make_client: Any, seed: dict[str, Any]) -> None:
    populate(seed)
    client = make_client("a_analyst")
    template = client.get("/api/v1/screenings/batch/template")
    assert template.status_code == 200 and template.headers["content-type"].startswith("text/csv")
    assert template.text.splitlines()[0] == HEADER.strip()
    reply = upload(client, template.text)
    assert reply.status_code == 202 and reply.json()["accepted_rows"] == 2
    status = client.get(f"/api/v1/screenings/batch/{reply.json()['id']}").json()
    assert status["status"] == "completed" and status["progress"] == 1.0
    assert status["completed"] == 2 and status["failed"] == 0


def test_upload_processes_and_reports_progress(make_client: Any, seed: dict[str, Any]) -> None:
    populate(seed)
    client = make_client("a_analyst")
    rows = [
        "Viktor Petrovich Example,person,RU,1970-01-01,,,",
        "Northern Star Shipping Ltd,organization,PA,,,,",
        "Unrelated Person,person,,,,,",
    ]
    reply = upload(client, HEADER + "\n".join(rows) + "\n")
    batch = reply.json()["id"]
    status = client.get(f"/api/v1/screenings/batch/{batch}").json()
    assert status["total"] == 3 and status["completed"] == 3 and status["status"] == "completed"
    by_name = {r["name"]: r for r in status["results"]}
    assert by_name["Viktor Petrovich Example"]["risk_level"] in {"high", "critical"}
    assert by_name["Unrelated Person"]["risk_level"] == "low"
    usage = seed["admin"].execute("SELECT batch_rows FROM usage_daily").fetchone()[0]
    assert usage == 3


def test_invalid_rows_are_reported_and_valid_rows_still_run(
    make_client: Any, seed: dict[str, Any]
) -> None:
    populate(seed)
    client = make_client("a_analyst")
    rows = [
        "Good Name,person,GB,,,,",
        ",person,,,,,",
        "Bad Kind,vessel,,,,,",
        "Bad Date,person,,yesterday,,,",
        "Fine Corp Ltd,organization,,,,,",
    ]
    reply = upload(client, HEADER + "\n".join(rows) + "\n").json()
    assert reply["accepted_rows"] == 2 and len(reply["rejected_rows"]) == 3
    assert {r["row"] for r in reply["rejected_rows"]} == {2, 3, 4}
    status = client.get(f"/api/v1/screenings/batch/{reply['id']}").json()
    assert status["completed"] == 2 and status["failed"] == 3 and status["status"] == "completed"
    assert [e["row_no"] for e in status["errors"]] == [2, 3, 4]
    assert "kind" in status["errors"][1]["error"]


def test_processing_failure_is_isolated_to_its_row(
    make_client: Any, seed: dict[str, Any], monkeypatch: Any
) -> None:
    from atlas.api import batch as batchmod

    real = batchmod.run_screening

    def flaky(conn: Any, tenant: Any, subject: Any, *args: Any, **kwargs: Any) -> Any:
        if subject["name"] == "Explode Me":
            raise RuntimeError("simulated failure")
        return real(conn, tenant, subject, *args, **kwargs)

    monkeypatch.setattr(batchmod, "run_screening", flaky)
    client = make_client("a_analyst")
    reply = upload(
        client, HEADER + "First One,person,,,,,\nExplode Me,person,,,,,\nThird,person,,,,,\n"
    )
    status = client.get(f"/api/v1/screenings/batch/{reply.json()['id']}").json()
    assert status["completed"] == 2 and status["failed"] == 1 and status["status"] == "completed"
    assert status["errors"][0]["row_no"] == 2
    assert "RuntimeError" in status["errors"][0]["error"]
    names = {r["name"] for r in status["results"]}
    assert names == {"First One", "Third"}
    assert "simulated" not in str(status)  # error text carries the type only, never row content


def test_all_rows_failing_marks_the_batch_failed(
    make_client: Any, seed: dict[str, Any], monkeypatch: Any
) -> None:
    from atlas.api import batch as batchmod

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("down")

    monkeypatch.setattr(batchmod, "run_screening", boom)
    client = make_client("a_analyst")
    reply = upload(client, HEADER + "One,person,,,,,\nTwo,person,,,,,\n")
    status = client.get(f"/api/v1/screenings/batch/{reply.json()['id']}").json()
    assert status["status"] == "failed" and status["failed"] == 2


def test_input_is_wiped_after_processing(make_client: Any, seed: dict[str, Any]) -> None:
    client = make_client("a_analyst")
    upload(client, HEADER + "Secret Person,person,,1980-05-05,passport,P1234567,\n")
    rows = seed["admin"].execute("SELECT payload_enc, status FROM batch_rows").fetchall()
    assert rows and all(r[0] is None and r[1] == "done" for r in rows)


def test_file_validation(make_client: Any, seed: dict[str, Any]) -> None:
    client = make_client("a_analyst")
    assert upload(client, "name\nOnly Name\n").status_code == 400  # kind column missing
    assert upload(client, HEADER).status_code == 400  # no data rows
    assert upload(client, b"\xff\xfe\x00bad").status_code == 400  # not UTF-8
    many = HEADER + "".join(f"Person {i},person,,,,,\n" for i in range(10_001))
    too_many = upload(client, many)
    assert too_many.status_code == 400 and "10000" in too_many.json()["detail"]
    ok = HEADER + "".join(f"Person {i},person,,,,,\n" for i in range(10_000))
    assert len(ok) < 5_000_000


def test_export_neutralises_formulas(make_client: Any, seed: dict[str, Any]) -> None:
    client = make_client("a_analyst")
    reply = upload(
        client, HEADER + '"=HYPERLINK(""http://x"")",person,,,,,\nPlain Name,person,,,,,\n'
    )
    batch = reply.json()["id"]
    export = client.get(f"/api/v1/screenings/batch/{batch}/export")
    assert export.status_code == 200 and "attachment" in export.headers["content-disposition"]
    lines = export.text.splitlines()
    assert lines[0].startswith("row,status,name")
    assert "'=HYPERLINK" in export.text and ",=HYPERLINK" not in export.text
    assert len(lines) == 3


def test_batch_is_private_to_its_tenant_and_role(make_client: Any, seed: dict[str, Any]) -> None:
    owner = make_client("a_analyst")
    batch = upload(owner, HEADER + "One,person,,,,,\n").json()["id"]
    other = make_client("b_analyst")
    assert other.get(f"/api/v1/screenings/batch/{batch}").status_code == 404
    assert other.get(f"/api/v1/screenings/batch/{batch}/export").status_code == 404
    assert upload(make_client("a_viewer"), HEADER + "One,person,,,,,\n").status_code == 403
    assert make_client("a_viewer").get(f"/api/v1/screenings/batch/{batch}").status_code == 200
    assert make_client().get("/api/v1/screenings/batch/template").status_code == 401


def test_large_upload_of_ten_thousand_rows(make_client: Any, seed: dict[str, Any]) -> None:
    client = make_client("a_analyst")
    text = HEADER + "".join(f"Bulk Person {i},person,,,,,\n" for i in range(10_000))
    reply = upload(client, text)
    assert reply.status_code == 202 and reply.json()["accepted_rows"] == 10_000
    status = client.get(f"/api/v1/screenings/batch/{reply.json()['id']}").json()
    assert status["completed"] == 10_000 and status["failed"] == 0 and status["progress"] == 1.0


def test_celery_dispatch_when_a_broker_is_configured(
    make_client: Any, seed: dict[str, Any], monkeypatch: Any
) -> None:
    sent: list[tuple[Any, ...]] = []
    from atlas.jobs import celery_app

    monkeypatch.setattr(celery_app, "send", lambda task, *args: sent.append((task, *args)))
    settings = seed["state"].settings
    settings.celery_broker_url = "redis://127.0.0.1:6379/1"
    try:
        client = make_client("a_analyst")
        reply = upload(client, HEADER + "Queued Person,person,,,,,\n").json()
    finally:
        settings.celery_broker_url = None
    assert sent == [("atlas.process_batch", str(seed["tenants"]["A"]), reply["id"])]
    status = client.get(f"/api/v1/screenings/batch/{reply['id']}").json()
    assert status["status"] == "queued" and status["progress"] == 0.0  # the worker has not run


def test_worker_processes_a_queued_batch(
    make_client: Any, seed: dict[str, Any], worker: Any, monkeypatch: Any
) -> None:
    from atlas.api.batch import process_batch
    from atlas.jobs import celery_app

    monkeypatch.setattr(celery_app, "send", lambda *a: None)
    seed["state"].settings.celery_broker_url = "redis://x"
    try:
        client = make_client("a_analyst")
        batch = upload(client, HEADER + "Queued One,person,,,,,\nQueued Two,person,,,,,\n").json()[
            "id"
        ]
    finally:
        seed["state"].settings.celery_broker_url = None
    process_batch(worker, seed["tenants"]["A"], __import__("uuid").UUID(batch))
    process_batch(worker, seed["tenants"]["A"], __import__("uuid").UUID(batch))  # safe to repeat
    status = client.get(f"/api/v1/screenings/batch/{batch}").json()
    assert status["status"] == "completed" and status["completed"] == 2
