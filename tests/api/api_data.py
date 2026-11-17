"""Seed rows for API tests: subjects, alerts, cases, watchlist entries, entities."""

from __future__ import annotations

from typing import Any

from atlas.names import normalize_name


def populate(seed: dict[str, Any]) -> dict[str, Any]:
    db = seed["admin"]
    out: dict[str, Any] = {}
    for tenant in ("A", "B"):
        tid = seed["tenants"][tenant]
        name = f"Subject of tenant {tenant}"
        norm = normalize_name(name, "organization").stripped
        sid = db.execute(
            "INSERT INTO subjects (tenant_id, kind, name, name_norm) VALUES (%s, 'organization', "
            "%s, %s) RETURNING id",
            (tid, name, norm),
        ).fetchone()[0]
        aid = db.execute(
            "INSERT INTO alerts (tenant_id, subject_id, severity) VALUES (%s, %s, 'high') "
            "RETURNING id",
            (tid, sid),
        ).fetchone()[0]
        cid = db.execute(
            "INSERT INTO cases (tenant_id, title) VALUES (%s, %s) RETURNING id",
            (tid, f"Case {tenant}"),
        ).fetchone()[0]
        db.execute("INSERT INTO case_alerts VALUES (%s, %s)", (cid, aid))
        out[tenant] = {"subject": sid, "alert": aid, "case": cid}
    source = db.execute(
        "INSERT INTO watchlist_sources (code, name, license, commercial_use, list_type) "
        "VALUES ('ofac_sdn', 'OFAC SDN', 'US Government work', true, 'sanctions') RETURNING id"
    ).fetchone()[0]
    for name, kind, countries, years in (
        ("Viktor Petrovich Example", "person", ["RU"], [1970]),
        ("Northern Star Shipping Ltd", "organization", ["PA"], []),
    ):
        norm = normalize_name(name, kind)
        db.execute(
            "INSERT INTO watchlist_entries (source_id, source_key, kind, name, name_norm, "
            "name_key, programs, countries, birth_years) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (source, name[:10], kind, name, norm.stripped, norm.key, ["TEST"], countries, years),
        )
    ids = []
    for qid, name in (("Q1", "Alpha Holdings"), ("Q2", "Beta Trading"), ("Q3", "Gamma Capital")):
        norm = normalize_name(name, "organization")
        ids.append(
            db.execute(
                "INSERT INTO entities (kind, qid, name, name_norm, name_key, source) "
                "VALUES ('organization', %s, %s, %s, %s, 'wikidata') RETURNING id",
                (qid, name, norm.stripped, norm.key),
            ).fetchone()[0]
        )
    db.execute(
        "INSERT INTO entity_relationships (src_id, dst_id, rel_type, source) VALUES "
        "(%s, %s, 'owns', 'test'), (%s, %s, 'owns', 'test')",
        (ids[0], ids[1], ids[1], ids[2]),
    )
    out["entities"] = ids
    return out
