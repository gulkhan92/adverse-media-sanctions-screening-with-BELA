"""Data quality checks: pandera schemas on loaded tables plus SQL assertions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd
import pandera.pandas as pa
import psycopg

ISO_ALPHA2 = frozenset(
    "AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ "
    "BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM "
    "DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS "
    "GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN "
    "KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ "
    "MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM "
    "PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV "
    "SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI "
    "VN VU WF WS YE YT ZA ZM ZW XK".split()
)

WATCHLIST_SCHEMA = pa.DataFrameSchema(
    {
        "source_key": pa.Column(str, nullable=False),
        "kind": pa.Column(str, pa.Check.isin(["person", "organization", "vessel", "aircraft"])),
        "name": pa.Column(str, pa.Check(lambda s: s.str.strip().str.len() > 0)),
        "name_norm": pa.Column(str, pa.Check(lambda s: s.str.len() > 0)),
        "birth_years": pa.Column(object, pa.Check(lambda s: s.map(_valid_years)), nullable=False),
    },
    strict=False,
)


def _valid_years(years: Any) -> bool:
    return all(1850 <= int(y) <= 2026 for y in years)


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str


def _one(conn: psycopg.Connection[Any], sql: str) -> Any:
    with conn.cursor() as cur:
        cur.execute(sql)
        row = cur.fetchone()
        return row[0] if row else None


def run_checks(conn: psycopg.Connection[Any]) -> list[CheckResult]:
    results: list[CheckResult] = []

    def check(name: str, passed: bool, detail: str) -> None:
        results.append(CheckResult(name, bool(passed), detail))

    for table, column in [
        ("watchlist_entries", "name"),
        ("watchlist_aliases", "alias"),
        ("entities", "name"),
        ("entity_aliases", "alias"),
    ]:
        bad = _one(
            conn, f"SELECT count(*) FROM {table} WHERE {column} IS NULL OR btrim({column}) = ''"
        )
        check(f"no null or blank names: {table}.{column}", bad == 0, f"{bad} offending rows")
    bad = _one(
        conn,
        "SELECT count(*) FROM icij.nodes WHERE node_type <> 'address' AND name IS NULL",
    )
    total = _one(conn, "SELECT count(*) FROM icij.nodes WHERE node_type <> 'address'")
    # ICIJ publishes some unnamed nodes; report the share rather than fail.
    check(
        "icij non-address nodes without a name below 1 percent",
        bad <= 0.01 * total,
        f"{bad} of {total}",
    )

    for table in ("watchlist_entries", "entities"):
        with conn.cursor() as cur:
            cur.execute(f"SELECT DISTINCT unnest(countries) FROM {table}")
            invalid = sorted(c for (c,) in cur.fetchall() if c not in ISO_ALPHA2)
        check(f"valid ISO 3166-1 country codes: {table}", not invalid, f"invalid: {invalid[:10]}")

    dup = _one(
        conn,
        "SELECT count(*) FROM (SELECT source_id, source_key FROM watchlist_entries "
        "GROUP BY 1, 2 HAVING count(*) > 1) d",
    )
    check("no duplicate watchlist keys", dup == 0, f"{dup} duplicate keys")
    dup = _one(
        conn,
        "SELECT count(*) FROM (SELECT qid FROM entities WHERE qid IS NOT NULL GROUP BY qid "
        "HAVING count(*) > 1) d",
    )
    check("no duplicate QIDs", dup == 0, f"{dup} duplicates")

    dims = _one(
        conn, "SELECT string_agg(DISTINCT vector_dims(embedding)::text, ',') FROM entity_embeddings"
    )
    check("vector dimension equals 1024", dims in (None, "1024"), f"dimensions present: {dims}")

    orphan = _one(
        conn,
        "SELECT count(*) FROM entity_embeddings e WHERE NOT EXISTS ("
        "SELECT 1 FROM entities x WHERE e.ref_type IN ('entity','entity_alias') "
        "AND x.id = e.owner_id "
        "UNION ALL SELECT 1 FROM watchlist_entries w WHERE e.ref_type IN "
        "('watchlist_entry','watchlist_alias') AND w.id = e.owner_id)",
    )
    check("embeddings reference existing rows", orphan == 0, f"{orphan} orphans")

    long_snippet = _one(conn, "SELECT count(*) FROM articles WHERE char_length(snippet) > 300")
    check("article snippets at most 300 characters", long_snippet == 0, f"{long_snippet} rows")

    with conn.cursor() as cur:
        cur.execute("SELECT source_key, kind, name, name_norm, birth_years FROM watchlist_entries")
        columns = [d.name for d in cur.description or []]
        frame = pd.DataFrame(cur.fetchall(), columns=columns)
    try:
        WATCHLIST_SCHEMA.validate(frame, lazy=True)
        check("pandera schema: watchlist_entries", True, f"{len(frame)} rows validated")
    except pa.errors.SchemaErrors as exc:
        check("pandera schema: watchlist_entries", False, str(exc.failure_cases.head(5)))
    return results


def statistics(conn: psycopg.Connection[Any]) -> dict[str, Any]:
    stats: dict[str, Any] = {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT s.code, s.entry_count, (SELECT count(*) FROM watchlist_aliases a "
            "JOIN watchlist_entries e ON e.id = a.entry_id WHERE e.source_id = s.id) "
            "FROM watchlist_sources s"
        )
        stats["watchlists"] = cur.fetchall()
        cur.execute(
            "SELECT source, count(*), count(qid), count(lei), "
            "round(100.0 * count(qid) / count(*), 1) FROM entities GROUP BY source ORDER BY 1"
        )
        stats["entities_by_source"] = cur.fetchall()
        cur.execute("SELECT count(*) FROM entity_aliases")
        stats["entity_aliases"] = cur.fetchone()[0]  # type: ignore[index]
        cur.execute(
            "SELECT rel_type, source, count(*) FROM entity_relationships "
            "GROUP BY 1, 2 ORDER BY 3 DESC"
        )
        stats["relationships"] = cur.fetchall()
        cur.execute("SELECT node_type, count(*) FROM icij.nodes GROUP BY 1 ORDER BY 2 DESC")
        stats["icij_nodes"] = cur.fetchall()
        cur.execute("SELECT count(*) FROM icij.relationships")
        stats["icij_relationships"] = cur.fetchone()[0]  # type: ignore[index]
        cur.execute("SELECT language, count(*) FROM articles GROUP BY 1 ORDER BY 2 DESC")
        stats["articles_by_language"] = cur.fetchall()
        cur.execute(
            "SELECT ref_type, model_version, count(*) FROM entity_embeddings "
            "GROUP BY 1, 2 ORDER BY 1"
        )
        stats["embeddings"] = cur.fetchall()
    return stats
