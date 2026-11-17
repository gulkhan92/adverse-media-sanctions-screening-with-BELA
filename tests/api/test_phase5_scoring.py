"""Risk score formula, configuration bounds, and screening retrieval (identifiers, vectors)."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from api_data import populate
from atlas.api.schemas import RiskConfig
from atlas.api.scoring import DEFAULT_SCORING, compute_score, level_for, recency_decay
from atlas.api.screening import DEFAULT_CONFIG, Embedder
from atlas.embeddings import HashingEmbedder
from media_data import add_media
from pgvector.psycopg import register_vector

NOW = dt.datetime(2026, 11, 17, tzinfo=dt.UTC)


def config() -> dict[str, Any]:
    return RiskConfig().model_dump()


def test_formula_components_and_total() -> None:
    result = compute_score(
        config(),
        {"confidence": 0.9, "list_type": "sanctions"},
        [{"severity": 3, "link_confidence": 0.8, "confidence": 0.9, "published_at": NOW}],
        NOW,
    )
    c = result["components"]
    assert c["list"]["points"] == pytest.approx(100 * 0.40 * 1.0 * 0.9)
    assert c["adverse"]["points"] == pytest.approx(100 * 0.35 * 0.75 * 0.8)
    assert c["recency"]["points"] == pytest.approx(100 * 0.15 * 1.0)
    assert c["source"]["points"] == pytest.approx(100 * 0.10 * 1.0)
    expected = 36 + 21 + 15 + 10
    assert result["score"] == pytest.approx(expected, abs=0.01)
    assert result["level"] == "critical"  # 82 is at or above 80
    assert abs(sum(v["points"] for v in c.values()) - result["score"]) < 0.02


def test_no_evidence_scores_zero() -> None:
    result = compute_score(config(), None, [], NOW)
    assert result["score"] == 0 and result["level"] == "low"


def test_adverse_only_uses_the_adverse_only_list_weight() -> None:
    item = {"severity": 4, "link_confidence": 1.0, "confidence": 1.0, "published_at": NOW}
    only = compute_score(config(), None, [item], NOW)
    assert only["components"]["list"]["list_weight"] == 0.4
    assert only["components"]["list"]["points"] == pytest.approx(16.0)
    on_list = compute_score(config(), {"confidence": 1.0, "list_type": "pep"}, [item], NOW)
    assert on_list["components"]["list"]["list_weight"] == 0.6


def test_list_weights_by_type() -> None:
    def pts(kind: str) -> float:
        return compute_score(config(), {"confidence": 1.0, "list_type": kind}, [], NOW)[
            "components"
        ]["list"]["points"]

    assert pts("sanctions") == 40.0 and pts("pep") == pytest.approx(24.0)


def test_level_boundaries() -> None:
    t = DEFAULT_SCORING["thresholds"]
    assert [level_for(x, t) for x in (39.99, 40, 59.99, 60, 79.99, 80, 100)] == [
        "low",
        "medium",
        "medium",
        "high",
        "high",
        "critical",
        "critical",
    ]


def test_recency_half_life() -> None:
    assert recency_decay(NOW, NOW, 365) == pytest.approx(1.0)
    assert recency_decay(NOW - dt.timedelta(days=365), NOW, 365) == pytest.approx(0.5)
    assert recency_decay(NOW - dt.timedelta(days=730), NOW, 365) == pytest.approx(0.25)
    assert recency_decay(None, NOW, 365) == 0.25


def test_older_evidence_lowers_the_score() -> None:
    def score(age_days: int) -> float:
        item = {
            "severity": 4,
            "link_confidence": 1.0,
            "confidence": 1.0,
            "published_at": NOW - dt.timedelta(days=age_days),
        }
        return float(compute_score(config(), None, [item], NOW)["score"])

    assert score(0) > score(365) > score(1500)


@pytest.mark.parametrize(
    "change",
    [
        {"weights": {"list": 0.7, "adverse": 0.1, "recency": 0.1, "source": 0.1}},
        {"weights": {"list": 0.4, "adverse": 0.4, "recency": 0.15, "source": 0.10}},
        {"weights": {"list": 0.01, "adverse": 0.5, "recency": 0.29, "source": 0.2}},
        {"thresholds": {"medium": 10.0, "high": 60.0, "critical": 80.0}},
        {"thresholds": {"medium": 40.0, "high": 60.0, "critical": 99.0}},
        {"thresholds": {"medium": 50.0, "high": 50.0, "critical": 80.0}},
        {"list_weights": {"sanctions": 0.3, "pep": 0.6, "other": 0.5, "adverse_only": 0.4}},
        {"recency_half_life_days": 5},
        {"unknown_field": 1},
    ],
)
def test_configuration_outside_bounds_is_rejected(change: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        RiskConfig.model_validate({**DEFAULT_CONFIG, **change})


def test_configuration_within_bounds_is_accepted() -> None:
    ok = RiskConfig.model_validate(
        {
            **DEFAULT_CONFIG,
            "weights": {"list": 0.35, "adverse": 0.35, "recency": 0.15, "source": 0.15},
            "thresholds": {"medium": 45.0, "high": 65.0, "critical": 85.0},
        }
    )
    assert ok.thresholds.critical == 85.0


def test_api_rejects_out_of_bounds_config_and_applies_valid_one(
    make_client: Any, seed: dict[str, Any]
) -> None:
    populate(seed)
    admin = make_client("a_admin")
    bad = {**DEFAULT_CONFIG, "thresholds": {"medium": 5.0, "high": 60.0, "critical": 80.0}}
    assert admin.put("/api/v1/admin/risk-config", json=bad).status_code == 422
    subject = {"subject": {"name": "Viktor Petrovich Example", "kind": "person"}}
    before = admin.post("/api/v1/screenings", json=subject).json()
    strict = {**DEFAULT_CONFIG, "thresholds": {"medium": 50.0, "high": 75.0, "critical": 95.0}}
    saved = admin.put("/api/v1/admin/risk-config", json=strict)
    assert saved.status_code == 200 and saved.json()["version"] == 1
    after = admin.post("/api/v1/screenings", json=subject).json()
    assert before["risk_level"] == "high" and after["risk_level"] == "medium"
    assert after["risk_score"] == before["risk_score"]
    assert admin.get("/api/v1/admin/risk-config").json()["config"]["thresholds"]["high"] == 75.0


def test_screening_returns_and_stores_the_breakdown(make_client: Any, seed: dict[str, Any]) -> None:
    populate(seed)
    client = make_client("a_analyst")
    body = client.post(
        "/api/v1/screenings",
        json={"subject": {"name": "Viktor Petrovich Example", "kind": "person"}},
    ).json()
    parts = body["score_breakdown"]["components"]
    assert set(parts) == {"list", "adverse", "recency", "source"}
    assert body["score_breakdown"]["version"] == "score-v1"
    stored = client.get(f"/api/v1/screenings/{body['id']}").json()
    assert stored["score_breakdown"]["score"] == body["risk_score"]
    assert parts["list"]["list_weight"] == 1.0  # the test list is typed as sanctions


def test_adverse_media_raises_the_score_and_adds_evidence(
    make_client: Any, seed: dict[str, Any]
) -> None:
    ids = populate(seed)
    client = make_client("a_analyst")
    subject = {"subject": {"name": "Viktor Petrovich Example", "kind": "person"}}
    plain = client.post("/api/v1/screenings", json=subject).json()
    add_media(seed, ids)
    with_media = client.post("/api/v1/screenings", json=subject).json()
    assert with_media["risk_score"] > plain["risk_score"]
    media = [m for m in with_media["matches"] if m["match_type"] == "adverse_media"]
    assert media and media[0]["evidence"].startswith("Alpha Holdings was accused")
    detail = client.get(f"/api/v1/screenings/{with_media['id']}").json()
    evidence = [m for m in detail["matches"] if m["match_type"] == "adverse_media"]
    assert evidence[0]["article_url"] == "https://news.example/a1"
    assert with_media["score_breakdown"]["components"]["adverse"]["points"] > 0


def test_adverse_only_subject_is_found_by_entity_name(
    make_client: Any, seed: dict[str, Any]
) -> None:
    ids = populate(seed)
    add_media(seed, ids)
    body = (
        make_client("a_analyst")
        .post(
            "/api/v1/screenings",
            json={"subject": {"name": "Alpha Holdings", "kind": "organization"}},
        )
        .json()
    )
    assert body["matches"] and body["matches"][0]["features"]["via"] == "entity_name"
    assert body["score_breakdown"]["components"]["list"]["list_weight"] == 0.4
    assert body["risk_level"] in {"medium", "high"}


def test_exact_identifier_match(make_client: Any, seed: dict[str, Any]) -> None:
    populate(seed)
    seed["admin"].execute(
        "UPDATE watchlist_entries SET identifiers = %s::jsonb WHERE name LIKE 'Northern%%'",
        ('[{"type": "LEI", "value": "5493001KJTIIGC8Y1R12"}]',),
    )
    client = make_client("a_analyst")
    reply = client.post(
        "/api/v1/screenings",
        json={
            "subject": {
                "name": "Completely Different Name Ltd",
                "kind": "organization",
                "identifiers": [{"type": "LEI", "value": "5493001KJTIIGC8Y1R12"}],
            }
        },
    ).json()
    top = reply["matches"][0]
    assert top["name"] == "Northern Star Shipping Ltd" and top["confidence"] == 1.0
    assert top["features"]["via"] == "identifier"
    stored = (
        seed["admin"]
        .execute("SELECT identifiers_enc FROM subjects WHERE name LIKE 'Completely%'")
        .fetchone()[0]
    )
    assert b"5493001" not in bytes(stored)
    again = client.post("/api/v1/screenings", json={"subject_id": reply["subject"]["id"]}).json()
    assert again["matches"][0]["features"]["via"] == "identifier"  # identifiers are reloaded


def test_vector_retrieval_adds_matches_and_reports_status(
    make_client: Any, seed: dict[str, Any]
) -> None:
    populate(seed)
    hashing = HashingEmbedder()
    db = seed["admin"]
    register_vector(db)
    for entry_id, name_norm in db.execute("SELECT id, name_norm FROM watchlist_entries"):
        db.execute(
            "INSERT INTO entity_embeddings (ref_type, ref_id, owner_id, name_norm, embedding, "
            "model_version) VALUES ('watchlist_entry', %s, %s, %s, %s, %s)",
            (entry_id, entry_id, name_norm, hashing.encode([name_norm])[0], hashing.model_version),
        )
    state = seed["state"]
    state.extras["embedder"] = Embedder(
        hashing.model_version, lambda name: hashing.encode([name])[0]
    )
    try:
        client = make_client("a_analyst")
        reply = client.post(
            "/api/v1/screenings",
            json={"subject": {"name": "Northern Star Shipping Ltd", "kind": "organization"}},
        ).json()
        assert reply["notes"]["vector_search"] == "ok"
        assert reply["matches"][0]["name"] == "Northern Star Shipping Ltd"
        # An unavailable embedder is reported and does not break screening.
        state.extras["embedder"] = Embedder(hashing.model_version, lambda name: None)
        degraded = client.post(
            "/api/v1/screenings",
            json={"subject": {"name": "Northern Star Shipping Ltd", "kind": "organization"}},
        ).json()
        assert degraded["notes"]["vector_search"] == "unavailable" and degraded["matches"]
    finally:
        state.extras.pop("embedder", None)
