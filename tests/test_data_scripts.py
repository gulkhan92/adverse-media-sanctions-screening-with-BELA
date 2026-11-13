from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import common
import fetch_article_text as fat
import harvest_gdelt as hg
import pytest
from build_wikidata_subset import classify, subclass_closure
from download_mewsli_x import convert_row
from verify_harvest import evaluate


def test_sha256_and_manifest_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(common, "DATA_DIR", tmp_path)
    monkeypatch.setattr(common, "MANIFEST_PATH", tmp_path / "manifest.yaml")
    sample = tmp_path / "raw" / "a.txt"
    sample.parent.mkdir()
    sample.write_text("hello")
    assert not common.file_is_current("src", sample)
    entry = common.record_file("src", sample)
    assert entry["size_bytes"] == 5
    assert common.file_is_current("src", sample)
    sample.write_text("changed")
    assert not common.file_is_current("src", sample)


def test_private_addresses_are_blocked() -> None:
    for address in ("127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254", "::1"):
        assert not fat.is_public_address(address)
    assert fat.is_public_address("8.8.8.8")
    with pytest.raises(fat.BlockedUrl):
        fat.validate_url("http://127.0.0.1/admin")
    with pytest.raises(fat.BlockedUrl):
        fat.validate_url("file:///etc/passwd")


def test_snippet_is_capped() -> None:
    assert len(fat.make_snippet("word " * 500)) <= fat.SNIPPET_CHARS
    assert fat.make_snippet("short  text\n here") == "short text here"


def test_gdelt_query_matrix_is_7_by_7() -> None:
    assert len(hg.LANGUAGES) == 7 and len(hg.TOPICS) == 7
    assert hg.build_query("german", "bribery") == "Bestechung sourcelang:german"


def test_gdelt_dedup_by_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hg, "OUT_DIR", tmp_path)
    row = hg.normalize(
        {"url": "https://a.test/1", "seendate": "20261109T101500Z"}, "english", "fraud"
    )
    assert hg.append_day_file([row, row]) == 1
    assert hg.append_day_file([row]) == 0
    lines = (tmp_path / "2026-11-09.jsonl").read_text().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["language"] == "en"


def test_wikidata_classification() -> None:
    parents = {"Q1": ["Q43229"], "Q2": ["Q1"], "Q9": ["Q999"]}
    classes = subclass_closure({"Q43229"}, parents)
    assert classes == {"Q43229", "Q1", "Q2"}
    assert classify(["Q5"], classes) == "person"
    assert classify(["Q2"], classes) == "organization"
    assert classify(["Q9"], classes) is None


def test_mewsli_conversion() -> None:
    row = {
        "mention": "Jelena Hoffmann: Es treten { Jelena Hoffmann } (SPD) auf.",
        "entity_id": "Q1686648",
    }
    out = convert_row(row, "de")
    assert out is not None
    assert out["text"] == "Es treten Jelena Hoffmann (SPD) auf."
    assert out["text"][out["start"] : out["end"]] == "Jelena Hoffmann"
    assert out["qid"] == "Q1686648" and out["lang"] == "de"
    assert convert_row({"mention": "no marker", "entity_id": "Q1"}, "de") is None


def test_harvest_health_evaluation() -> None:
    now = datetime.now(UTC)

    def cycle(minutes_ago: int, errors: int = 0) -> dict[str, object]:
        end = now - timedelta(minutes=minutes_ago)
        return {
            "started": (end - timedelta(minutes=5)).isoformat(),
            "finished": end.isoformat(),
            "queries": 49,
            "errors": errors,
        }

    healthy = [cycle(m) for m in range(60, 48 * 60 + 61, 30)][::-1]
    assert evaluate(healthy, 48, 90) == []
    gap = [cycle(600), cycle(10)]
    assert any("gap" in p for p in evaluate(gap, 12, 90))
    assert any("failed" in p for p in evaluate([cycle(10, errors=49)], 1, 90))


def test_wikidata_subset_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import gzip
    import sys

    import build_wikidata_subset as bws
    import pyarrow.parquet as pq

    def item(qid: str, p31: str, links: int, p279: str | None = None) -> dict[str, object]:
        claims: dict[str, list[dict[str, object]]] = {
            "P31": [{"mainsnak": {"snaktype": "value", "datavalue": {"value": {"id": p31}}}}]
        }
        if p279:
            claims["P279"] = [
                {"mainsnak": {"snaktype": "value", "datavalue": {"value": {"id": p279}}}}
            ]
        return {
            "id": qid,
            "claims": claims,
            "sitelinks": {f"s{i}wiki": {} for i in range(links)},
            "labels": {"en": {"value": f"Label {qid}"}},
            "descriptions": {"en": {"value": "desc"}},
            "aliases": {"de": [{"value": f"Alias {qid}"}]},
        }

    entities = [
        item("Q100", "Q5", 30),
        item("Q101", "Q5", 2),  # below threshold
        item("Q102", "Q900", 30),  # organization through subclass
        item("Q900", "Q16889133", 1, p279="Q43229"),
        item("Q103", "Q5", 2),  # forced include
    ]
    dump = tmp_path / "dump.json.gz"
    with gzip.open(dump, "wt", encoding="utf-8") as handle:
        handle.write("[\n" + ",\n".join(json.dumps(e) for e in entities) + "\n]\n")
    forced = tmp_path / "qids.txt"
    forced.write_text("Q103\n")
    monkeypatch.setattr(bws, "PROCESSED_DIR", tmp_path / "out")
    monkeypatch.setattr(bws, "record_file", lambda *_: {})
    monkeypatch.setattr(sys, "argv", ["x", "--input", str(dump), "--include-qids", str(forced)])
    bws.main()
    table = pq.read_table(tmp_path / "out" / "wikidata" / "entities.parquet").to_pylist()
    assert {r["qid"]: r["kind"] for r in table} == {
        "Q100": "person",
        "Q102": "organization",
        "Q103": "person",
    }
