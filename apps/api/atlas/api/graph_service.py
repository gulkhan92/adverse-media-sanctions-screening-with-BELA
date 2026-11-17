"""Entity graph: registry and ownership relationships plus article co-occurrence edges.

Subgraphs are loaded breadth first from PostgreSQL into networkx. Expansion stops at the node
cap, and the result reports whether it was truncated. Every edge carries provenance: the
relationship type and source for registry edges, and the supporting article IDs with the total
sentence count for co-occurrence edges (weight).
"""

from __future__ import annotations

from typing import Any

import networkx as nx
import psycopg

from atlas.api.db import Row

NODE_CAP = 500
PATH_NODE_CAP = 5000
EDGE_FETCH = 2000


def _fetch_edges(
    conn: psycopg.Connection[Row], ids: list[int]
) -> list[tuple[int, int, dict[str, Any]]]:
    edges: list[tuple[int, int, dict[str, Any]]] = []
    for r in conn.execute(
        "SELECT src_id, dst_id, rel_type, source FROM entity_relationships "
        "WHERE src_id = ANY(%s) OR dst_id = ANY(%s) LIMIT %s",
        (ids, ids, EDGE_FETCH),
    ):
        edges.append(
            (
                r["src_id"],
                r["dst_id"],
                {
                    "kind": "relationship",
                    "rel_type": r["rel_type"],
                    "source": r["source"],
                    "directed": [r["src_id"], r["dst_id"]],
                },
            )
        )
    for r in conn.execute(
        "SELECT entity_a, entity_b, sum(sentences) AS weight, "
        "(array_agg(article_id ORDER BY article_id))[1:5] AS articles, count(*) AS article_count "
        "FROM entity_cooccurrence WHERE entity_a = ANY(%s) OR entity_b = ANY(%s) "
        "GROUP BY entity_a, entity_b LIMIT %s",
        (ids, ids, EDGE_FETCH),
    ):
        edges.append(
            (
                r["entity_a"],
                r["entity_b"],
                {
                    "kind": "cooccurrence",
                    "weight": int(r["weight"]),
                    "articles": r["articles"],
                    "article_count": r["article_count"],
                    "source": "articles",
                },
            )
        )
    return edges


def expand(
    conn: psycopg.Connection[Row], seeds: list[int], depth: int, cap: int = NODE_CAP
) -> tuple[nx.MultiGraph, bool]:
    """Breadth-first expansion from ``seeds``. Returns the graph and a truncation flag."""
    graph: nx.MultiGraph = nx.MultiGraph()
    graph.add_nodes_from(seeds)
    frontier = list(seeds)
    truncated = False
    for _ in range(depth):
        if not frontier:
            break
        new: list[int] = []
        for a, b, data in _fetch_edges(conn, frontier):
            for node in (a, b):
                if node not in graph:
                    if graph.number_of_nodes() >= cap:
                        truncated = True
                        continue
                    graph.add_node(node)
                    new.append(node)
            if a in graph and b in graph:
                graph.add_edge(a, b, **data)
        frontier = new
    return graph, truncated


def node_rows(conn: psycopg.Connection[Row], ids: list[int]) -> list[Row]:
    return conn.execute(
        "SELECT id, qid, kind, name, countries FROM entities WHERE id = ANY(%s) ORDER BY id", (ids,)
    ).fetchall()


def serialise(conn: psycopg.Connection[Row], graph: nx.MultiGraph) -> dict[str, Any]:
    edges = [{"source_id": a, "target_id": b, **data} for a, b, data in graph.edges(data=True)]
    return {"nodes": node_rows(conn, list(graph.nodes)), "edges": edges}


def neighbors(conn: psycopg.Connection[Row], entity_id: int, depth: int) -> dict[str, Any]:
    graph, truncated = expand(conn, [entity_id], depth, NODE_CAP)
    return {**serialise(conn, graph), "truncated": truncated, "node_cap": NODE_CAP}


def shortest_path(
    conn: psycopg.Connection[Row], src: int, dst: int, max_depth: int
) -> dict[str, Any]:
    """Shortest path by number of hops over the combined graph (registry and co-occurrence)."""
    graph, truncated = expand(conn, [src], max_depth, PATH_NODE_CAP)
    if dst not in graph:
        return {"found": False, "nodes": [], "edges": [], "truncated": truncated}
    simple = nx.Graph(graph)  # collapse parallel edges for hop counting
    nodes = nx.shortest_path(simple, src, dst)
    edges: list[dict[str, Any]] = []
    for a, b in zip(nodes, nodes[1:], strict=False):
        parallel = list(graph.get_edge_data(a, b).values())
        edges.append(
            {
                "source_id": a,
                "target_id": b,
                "evidence": parallel,
                "kinds": sorted({p["kind"] for p in parallel}),
            }
        )
    return {
        "found": True,
        "hops": len(nodes) - 1,
        "nodes": node_rows(conn, nodes),
        "edges": edges,
        "truncated": truncated,
    }
