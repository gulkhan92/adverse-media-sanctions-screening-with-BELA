"""Generate docs/data_quality_report.md: quality checks, coverage, retrieval, query plans."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from typing import Any

import numpy as np
import psycopg
from pgvector.psycopg import register_vector

from atlas import quality, resolution
from atlas.config import EMBEDDING_MODEL, REPO_ROOT
from atlas.db import connect
from atlas.embeddings import KB_PREDICATE, make_embedder
from atlas.evaluation import build_query_sets, evaluate_set

PLAN_QUERIES: list[tuple[str, str, tuple[Any, ...]]] = [
    (
        "Watchlist name, trigram similarity",
        "SELECT id FROM watchlist_entries WHERE name_norm %% %s ORDER BY name_norm <-> %s LIMIT 10",
        ("mohammad ali hassan", "mohammad ali hassan"),
    ),
    (
        "Watchlist alias, exact normalized match",
        "SELECT entry_id FROM watchlist_aliases WHERE alias_norm = %s",
        ("banco nacional de cuba",),
    ),
    (
        "Knowledge-base alias, exact normalized match",
        "SELECT entity_id FROM entity_aliases WHERE alias_norm = %s",
        ("angela merkel",),
    ),
    (
        "Entity name, trigram similarity",
        "SELECT id FROM entities WHERE name_norm %% %s ORDER BY name_norm <-> %s LIMIT 10",
        ("angela merkel", "angela merkel"),
    ),
    (
        "Graph neighbours, one hop",
        "SELECT dst_id FROM entity_relationships WHERE src_id = %s",
        (1,),
    ),
    (
        "ICIJ officer name, trigram similarity",
        "SELECT node_id FROM icij.nodes WHERE name_norm %% %s ORDER BY name_norm <-> %s LIMIT 10",
        ("john smith", "john smith"),
    ),
    (
        "Alert queue for one tenant",
        "SELECT id FROM alerts WHERE tenant_id = gen_random_uuid() AND status = 'new' "
        "ORDER BY severity, created_at DESC LIMIT 50",
        (),
    ),
]


def analyze(conn: psycopg.Connection[Any]) -> None:
    """Refresh planner statistics so reviewed plans reflect the loaded data."""
    conn.commit()
    conn.autocommit = True
    conn.execute("ANALYZE")
    conn.autocommit = False


def explain(conn: psycopg.Connection[Any]) -> list[tuple[str, str, list[str]]]:
    plans = []
    for title, sql, params in PLAN_QUERIES:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL enable_seqscan = on")
            cur.execute("EXPLAIN (COSTS OFF) " + sql, params)
            lines = [row[0] for row in cur.fetchall()]
        plans.append((title, sql.replace("%%", "%"), lines))
    return plans


def vector_plan(conn: psycopg.Connection[Any]) -> list[str]:
    register_vector(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT embedding FROM entity_embeddings WHERE ref_type = 'entity' LIMIT 1")
        row = cur.fetchone()
        stored = row[0].to_numpy() if row else None
    vec = stored if stored is not None else np.ones(1024, dtype=np.float32) / 32
    with conn.cursor() as cur:
        cur.execute(
            f"EXPLAIN (COSTS OFF) SELECT owner_id FROM entity_embeddings WHERE {KB_PREDICATE} "
            "ORDER BY embedding <=> %s LIMIT 10",
            (vec,),
        )
        return [row[0] for row in cur.fetchall()]


def table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return out


def build(model: str, with_eval: bool) -> str:
    model_label = EMBEDDING_MODEL if model == "bge-m3" else "char-ngram-hash-v1 (smoke test)"
    lines = [
        "# Data quality report",
        "",
        f"Generated: {datetime.now(UTC).isoformat(timespec='seconds')}. "
        f"Embedding model: {model_label}.",
        "",
    ]
    with connect() as conn:
        analyze(conn)
        stats = quality.statistics(conn)
        lines += ["## Row counts", ""]
        lines += table(
            ["Watchlist source", "Entries", "Aliases"], [list(r) for r in stats["watchlists"]]
        )
        lines += [""]
        lines += table(
            ["Entity source", "Entities", "With QID", "With LEI", "QID coverage %"],
            [list(r) for r in stats["entities_by_source"]],
        )
        lines += [
            "",
            f"Entity aliases: {stats['entity_aliases']:,}. "
            f"ICIJ relationships: {stats['icij_relationships']:,}.",
            "",
        ]
        lines += table(["ICIJ node type", "Nodes"], [list(r) for r in stats["icij_nodes"]])
        lines += [""]
        lines += table(
            ["Relationship type", "Source", "Rows"], [list(r) for r in stats["relationships"]]
        )
        lines += [""]
        lines += table(
            ["Article language", "Articles"], [list(r) for r in stats["articles_by_language"]]
        )
        lines += [""]
        lines += table(["Ref type", "Model", "Vectors"], [list(r) for r in stats["embeddings"]])

        checks = quality.run_checks(conn)
        lines += ["", "## Data quality checks", ""]
        lines += table(
            ["Check", "Result", "Detail"],
            [[c.name, "pass" if c.passed else "FAIL", c.detail] for c in checks],
        )

        cov = resolution.coverage(conn)
        lines += [
            "",
            "## Watchlist to knowledge-base link coverage",
            "",
            f"Eligible entries (persons and organizations): {cov['eligible_entries']:,}.",
            f"Accepted links: {cov['accepted_entries']:,} entries ({cov['accepted_pct']} percent). "
            f"Accepted or in review: {cov['linked_or_review']:,} "
            f"({cov['linked_or_review_pct']} percent). "
            f"Accepted links to an entity with a Wikidata QID: {cov['accepted_with_qid']:,}.",
            "",
        ]
        lines += table(
            ["Status", "Method", "Links", "Entries"],
            [[r["status"], r["method"], r["links"], r["entries"]] for r in cov["by_method"]],
        )

        if with_eval:
            embedder = make_embedder("hash" if model == "hash" else "bge")
            lines += ["", "## Vector retrieval evaluation", ""]
            rows = []
            for qs in build_query_sets(conn):
                r = evaluate_set(conn, embedder, qs)
                if not r["n"]:
                    rows.append([qs.name, 0, "-", "-", "-", "-", "-", "no queries"])
                    continue
                rows.append(
                    [
                        r["name"],
                        r["n"],
                        f"{r['recall@1']:.3f}",
                        f"{r['recall@5']:.3f}",
                        f"{r['recall@10']:.3f}",
                        f"{r['latency_p50_ms']:.1f}",
                        f"{r['latency_p95_ms']:.1f}",
                        f"{'met' if r['passed'] else 'NOT met'} (targets: "
                        f"R@1 {r['targets']['recall@1']}, R@5 {r['targets']['recall@5']})",
                    ]
                )
            lines += table(
                ["Query set", "Queries", "R@1", "R@5", "R@10", "p50 ms", "p95 ms", "Targets"], rows
            )
            lines += [
                "",
                "Query sets are built with a fixed seed. `perturbed` applies typos, character "
                "swaps, reversed token order, or case changes to watchlist names. `cross_script` "
                "queries non-Latin aliases against the Latin primary name. `multilingual` "
                "queries non-English Wikidata aliases against the knowledge base. Latency is the "
                "database round trip with HNSW `ef_search = 100`, excluding query encoding.",
            ]

        lines += ["", "## Query plans (reviewed)", ""]
        for title, sql, plan in explain(conn):
            lines += [f"**{title}**", "", "```", sql, "", *plan, "```", ""]
        lines += ["**Vector search, knowledge base partial HNSW index**", "", "```"]
        lines += [*vector_plan(conn), "```", ""]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=["bge-m3", "hash"], default="bge-m3")
    parser.add_argument("--no-eval", action="store_true")
    args = parser.parse_args()
    text = build(args.model, not args.no_eval)
    path = REPO_ROOT / "docs" / "data_quality_report.md"
    path.write_text(text, encoding="utf-8")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
