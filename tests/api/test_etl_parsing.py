from __future__ import annotations

from pathlib import Path

from atlas.etl.ofac import (
    Entry,
    birth_years,
    display_name,
    format_birth,
    load_csv_entries,
    split_programs,
    to_rows,
)
from atlas.etl.wikidata import birth_date
from atlas.evaluation import perturb


def test_display_name_reorders_persons_only() -> None:
    assert display_name("HASSAN, Ali", "person") == "Ali HASSAN"
    assert display_name("ACME, LTD", "organization") == "ACME, LTD"


def test_split_programs() -> None:
    assert split_programs("SDGT] [IFSR") == ["SDGT", "IFSR"]
    assert split_programs("CUBA") == ["CUBA"]
    assert split_programs(None) == []


def test_birth_formats() -> None:
    assert format_birth((1948, 12, 10), (1948, 12, 10)) == "1948-12-10"
    assert format_birth((1948, 12, 0), (1948, 12, 0)) == "1948-12"
    assert format_birth((1948, 1, 1), (1948, 12, 31)) == "1948"
    assert format_birth((1945, 1, 1), (1948, 12, 31)) == "1945/1948"
    assert birth_years((1945, 1, 1), (1947, 12, 31)) == {1945, 1946, 1947}
    assert birth_years((1900, 1, 1), (1990, 1, 1)) == {1900}  # wide ranges are not expanded


def test_wikidata_birth_date() -> None:
    assert birth_date("+1954-07-17T00:00:00Z") == "1954-07-17"
    assert birth_date("+1954-00-00T00:00:00Z") == "1954"
    assert birth_date("not a date") is None


def test_csv_loading_and_rows(tmp_path: Path) -> None:
    (tmp_path / "SDN.CSV").write_text(
        '1,"HASSAN, Ali","individual","SDGT] [IFSR",-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,"DOB 1970"\n'
        '2,"ACME TRADING CO., LTD.",-0- ,"CUBA",-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- \n'
        "\x1a\n",
        encoding="utf-8",
    )
    (tmp_path / "ALT.CSV").write_text('1,5,"aka","HASAN, Aly",-0- \n', encoding="utf-8")
    (tmp_path / "ADD.CSV").write_text('2,9,"1 Main St","Havana","Cuba",-0- \n', encoding="utf-8")
    entries = load_csv_entries(tmp_path)
    assert set(entries) == {"1", "2"}
    assert entries["1"].name == "Ali HASSAN" and entries["1"].kind == "person"
    assert entries["1"].programs == ["SDGT", "IFSR"]
    assert entries["2"].kind == "organization" and entries["2"].addresses[0]["country"] == "Cuba"
    entry_rows, alias_rows = to_rows(entries)
    assert len(entry_rows) == 2
    assert alias_rows["1"][0][1] == "aly hasan"  # alias_norm


def test_to_rows_skips_duplicate_aliases() -> None:
    e = Entry(key="1", name="Ali Hassan", kind="person", programs=[], remarks=None)
    e.aliases = [("Ali  Hassan", "aka", False), ("Aly Hasan", "aka", False)]
    _, aliases = to_rows({"1": e})
    assert [a[1] for a in aliases["1"]] == ["aly hasan"]


def test_perturb_changes_but_keeps_scale() -> None:
    import random

    rng = random.Random(1)
    outputs = {perturb("Mohammad Ali Hassan", rng) for _ in range(40)}
    assert "Mohammad Ali Hassan" not in outputs or len(outputs) > 1
    assert all(len(o) >= 15 for o in outputs)
