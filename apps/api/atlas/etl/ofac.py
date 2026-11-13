"""OFAC SDN loader.

Legacy CSV files (no header row) give names, types, programs, aliases, and addresses.
SDN_ADVANCED.XML adds dates of birth, nationalities, citizenships, and identity
documents, joined on the entity number (the XML ``FixedRef`` of each party).
"""

from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from lxml import etree

from atlas.db import connect
from atlas.names import normalize_name, script_of

NS = "{https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/ADVANCED_XML}"
SOURCE_CODE = "ofac_sdn"
NULL_MARK = "-0-"
KIND_BY_SDN_TYPE = {
    "": "organization",
    "individual": "person",
    "vessel": "vessel",
    "aircraft": "aircraft",
}
ALIAS_TYPES = {"aka": "aka", "fka": "fka", "nka": "nka"}
IDENTIFIER_FEATURES = ("swift", "digital currency", "website", "email", "legal entity")


@dataclass
class Entry:
    key: str
    name: str
    kind: str
    programs: list[str]
    remarks: str | None
    aliases: list[tuple[str, str, bool]] = field(default_factory=list)  # name, type, low quality
    countries: set[str] = field(default_factory=set)
    birth_dates: list[str] = field(default_factory=list)
    birth_years: set[int] = field(default_factory=set)
    identifiers: list[dict[str, str]] = field(default_factory=list)
    addresses: list[dict[str, str]] = field(default_factory=list)
    listed_on: date | None = None


def clean(value: str) -> str | None:
    value = value.strip()
    return None if value in ("", NULL_MARK) else value


def display_name(name: str, kind: str) -> str:
    """Persons are listed as 'LAST, First'. Return 'First LAST' for matching and display."""
    if kind == "person" and ", " in name:
        last, first = name.split(", ", 1)
        return f"{first} {last}"
    return name


def split_programs(value: str | None) -> list[str]:
    if not value:
        return []
    return [p.strip() for p in re.split(r"\]\s*\[|;", value.strip("[] ")) if p.strip()]


def read_csv(path: Path) -> list[list[str]]:
    with path.open(encoding="utf-8", errors="replace", newline="") as handle:
        return [row for row in csv.reader(handle) if len(row) >= 3 and row[0].strip().isdigit()]


def load_csv_entries(directory: Path) -> dict[str, Entry]:
    entries: dict[str, Entry] = {}
    for row in read_csv(directory / "SDN.CSV"):
        key, raw_name, raw_type = row[0], row[1].strip(), (clean(row[2]) or "").lower()
        kind = KIND_BY_SDN_TYPE.get(raw_type, "organization")
        entries[key] = Entry(
            key=key,
            name=display_name(raw_name, kind),
            kind=kind,
            programs=split_programs(clean(row[3]) if len(row) > 3 else None),
            remarks=clean(row[11]) if len(row) > 11 else None,
        )
    for row in read_csv(directory / "ALT.CSV"):
        entry = entries.get(row[0])
        if entry is None or len(row) < 4:
            continue
        alias_type = ALIAS_TYPES.get((clean(row[2]) or "aka").lower(), "aka")
        entry.aliases.append((display_name(row[3].strip(), entry.kind), alias_type, False))
    for row in read_csv(directory / "ADD.CSV"):
        entry = entries.get(row[0])
        if entry is None or len(row) < 5:
            continue
        entry.addresses.append(
            {
                "address": clean(row[2]) or "",
                "city_state": clean(row[3]) or "",
                "country": clean(row[4]) or "",
            }
        )
    return entries


def _date_text(node: Any) -> tuple[int, int, int] | None:
    if node is None:
        return None
    try:
        return (
            int(node.findtext(f"{NS}Year") or 0),
            int(node.findtext(f"{NS}Month") or 0),
            int(node.findtext(f"{NS}Day") or 0),
        )
    except ValueError:
        return None


def format_birth(start_from: tuple[int, int, int], start_to: tuple[int, int, int]) -> str:
    fy, fm, fd = start_from
    ty, tm, td = start_to
    if start_from == start_to and fy and fm and fd:
        return f"{fy:04d}-{fm:02d}-{fd:02d}"
    if fy == ty and fm == tm and fy and fm:
        return f"{fy:04d}-{fm:02d}"
    if fy == ty and fy:
        return f"{fy:04d}"
    return f"{fy:04d}/{ty:04d}"


def birth_years(start_from: tuple[int, int, int], start_to: tuple[int, int, int]) -> set[int]:
    lo, hi = start_from[0], start_to[0]
    if not lo or hi < lo or hi - lo > 10:
        return {lo} if lo else set()
    return set(range(lo, hi + 1))


def enrich_from_xml(path: Path, entries: dict[str, Entry]) -> dict[str, int]:
    """Add birth dates, nationalities, identifiers, and listing dates from SDN_ADVANCED.XML."""
    countries: dict[str, str] = {}
    country_names: dict[str, str] = {}
    feature_names: dict[str, str] = {}
    doc_types: dict[str, str] = {}
    locations: dict[str, set[str]] = {}
    identity_to_profile: dict[str, str] = {}
    stats = {"parties": 0, "birth_dates": 0, "nationalities": 0, "identifiers": 0, "listed": 0}

    reference = {f"{NS}{t}" for t in ("Country", "FeatureType", "IDRegDocType", "Location")}
    records = {f"{NS}{t}" for t in ("DistinctParty", "IDRegDocument", "SanctionsEntry")}
    # Pass 1: reference sets and locations (the file lists parties before some of them).
    for _, el in etree.iterparse(str(path), events=("end",), tag=reference, huge_tree=True):
        tag = el.tag[len(NS) :]
        if tag == "Country":
            countries[el.get("ID", "")] = el.get("ISO2", "")
            country_names[(el.text or "").strip().casefold()] = el.get("ISO2", "")
        elif tag == "FeatureType":
            feature_names[el.get("ID", "")] = (el.text or "").strip()
        elif tag == "IDRegDocType":
            doc_types[el.get("ID", "")] = (el.text or "").strip()
        elif tag == "Location":
            found = {
                countries.get(lc.get("CountryID", ""), "")
                for lc in el.findall(f"{NS}LocationCountry")
            }
            # Nationality locations carry the country name as a type 1 location part.
            for part in el.findall(f"{NS}LocationPart"):
                if part.get("LocPartTypeID") == "1":
                    for value in part.iter(f"{NS}Value"):
                        found.add(country_names.get((value.text or "").strip().casefold(), ""))
            locations[el.get("ID", "")] = found - {""}
        el.clear()
    # Pass 2: parties, identity documents, and listing events.
    for _, el in etree.iterparse(str(path), events=("end",), tag=records, huge_tree=True):
        tag = el.tag[len(NS) :]
        if tag == "DistinctParty":
            _read_party(el, entries, feature_names, locations, identity_to_profile, stats)
        elif tag == "IDRegDocument":
            _read_document(el, entries, doc_types, countries, identity_to_profile, stats)
        else:
            _read_listing(el, entries, stats)
        el.clear()
    for entry in entries.values():
        for address in entry.addresses:
            iso = country_names.get(address["country"].casefold())
            if iso:
                entry.countries.add(iso)
    return stats


def _read_party(
    el: Any,
    entries: dict[str, Entry],
    feature_names: dict[str, str],
    locations: dict[str, set[str]],
    identity_to_profile: dict[str, str],
    stats: dict[str, int],
) -> None:
    key = el.get("FixedRef", "")
    entry = entries.get(key)
    stats["parties"] += 1
    for identity in el.iter(f"{NS}Identity"):
        identity_to_profile[identity.get("ID", "")] = key
    if entry is None:
        return
    # The legacy ALT.CSV is Latin-only; native-script aliases exist only in the XML.
    known = {alias[0] for alias in entry.aliases} | {entry.name}
    for alias in el.iter(f"{NS}Alias"):
        text = " ".join(
            (v.text or "").strip()
            for v in alias.iter(f"{NS}NamePartValue")
            if (v.text or "").strip()
        )
        if text and text not in known and script_of(text) not in ("LATIN", "NONE"):
            known.add(text)
            entry.aliases.append((text, "aka", alias.get("LowQuality") == "true"))
    for feature in el.iter(f"{NS}Feature"):
        name = feature_names.get(feature.get("FeatureTypeID", ""), "")
        lowered = name.lower()
        for version in feature.findall(f"{NS}FeatureVersion"):
            if name == "Birthdate":
                start = version.find(f"{NS}DatePeriod/{NS}Start")
                if start is None:
                    continue
                lo, hi = _date_text(start.find(f"{NS}From")), _date_text(start.find(f"{NS}To"))
                if lo and hi:
                    entry.birth_dates.append(format_birth(lo, hi))
                    entry.birth_years |= birth_years(lo, hi)
                    stats["birth_dates"] += 1
            elif name in ("Nationality Country", "Citizenship Country"):
                for loc in version.findall(f"{NS}VersionLocation"):
                    found = locations.get(loc.get("LocationID", ""), set())
                    entry.countries |= found
                    stats["nationalities"] += len(found)
            elif any(token in lowered for token in IDENTIFIER_FEATURES):
                detail = version.find(f"{NS}VersionDetail")
                text = (detail.text or "").strip() if detail is not None else ""
                if text:
                    entry.identifiers.append({"type": name, "value": text})
                    stats["identifiers"] += 1


def _read_document(
    el: Any,
    entries: dict[str, Entry],
    doc_types: dict[str, str],
    countries: dict[str, str],
    identity_to_profile: dict[str, str],
    stats: dict[str, int],
) -> None:
    entry = entries.get(identity_to_profile.get(el.get("IdentityID", ""), ""))
    number = (el.findtext(f"{NS}IDRegistrationNo") or "").strip()
    if entry is None or not number:
        return
    entry.identifiers.append(
        {
            "type": doc_types.get(el.get("IDRegDocTypeID", ""), "Document"),
            "value": number,
            "country": countries.get(el.get("IssuedBy-CountryID", ""), ""),
        }
    )
    stats["identifiers"] += 1


def _read_listing(el: Any, entries: dict[str, Entry], stats: dict[str, int]) -> None:
    entry = entries.get(el.get("ProfileID", ""))
    event = el.find(f"{NS}EntryEvent/{NS}Date")
    parts = _date_text(event)
    if entry and parts and all(parts):
        entry.listed_on = date(*parts)
        stats["listed"] += 1


def to_rows(
    entries: dict[str, Entry],
) -> tuple[list[tuple[Any, ...]], dict[str, list[tuple[Any, ...]]]]:
    """Convert entries to watchlist_entries rows and per-key alias rows."""
    entry_rows: list[tuple[Any, ...]] = []
    alias_rows: dict[str, list[tuple[Any, ...]]] = defaultdict(list)
    for key, e in entries.items():
        norm_kind = "person" if e.kind == "person" else "organization"
        n = normalize_name(e.name, norm_kind)
        if not n.full:
            continue
        entry_rows.append(
            (
                key,
                e.kind,
                e.name,
                n.stripped,
                n.key,
                e.programs,
                sorted(e.countries),
                sorted(set(e.birth_dates)),
                sorted(e.birth_years),
                json.dumps(e.identifiers),
                json.dumps(e.addresses),
                e.remarks,
                e.listed_on,
            )
        )
        seen = {n.stripped}
        for alias, alias_type, low in e.aliases:
            an = normalize_name(alias, norm_kind)
            if not an.full or an.stripped in seen:
                continue
            seen.add(an.stripped)
            alias_rows[key].append((alias, an.stripped, an.key, alias_type, low, script_of(alias)))
    return entry_rows, alias_rows


def load(raw_dir: Path) -> dict[str, int]:
    """Replace the OFAC SDN source in the database. Returns row counts."""
    entries = load_csv_entries(raw_dir)
    xml_stats = enrich_from_xml(raw_dir / "SDN_ADVANCED.XML", entries)
    entry_rows, alias_rows = to_rows(entries)
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO watchlist_sources
                   (code, name, url, license, commercial_use, list_type, published_on)
                   VALUES (%s, %s, %s, %s, true, 'sanctions', %s)
                   ON CONFLICT (code) DO UPDATE SET loaded_at = now()
                   RETURNING id""",
                (
                    SOURCE_CODE,
                    "OFAC Specially Designated Nationals (SDN)",
                    "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports",
                    "US federal government work, public domain",
                    date.today(),
                ),
            )
            source_id = cur.fetchone()[0]  # type: ignore[index]
            cur.execute("DELETE FROM watchlist_entries WHERE source_id = %s", (source_id,))
            # Row ids change on reload, so vectors for the old ids are stale.
            cur.execute(
                "DELETE FROM entity_embeddings "
                "WHERE ref_type IN ('watchlist_entry', 'watchlist_alias')"
            )
        cols = [
            "source_key",
            "kind",
            "name",
            "name_norm",
            "name_key",
            "programs",
            "countries",
            "birth_dates",
            "birth_years",
            "identifiers",
            "addresses",
            "remarks",
            "listed_on",
        ]
        with conn.cursor() as cur:
            for row in entry_rows:
                cur.execute(
                    f"INSERT INTO watchlist_entries (source_id, {', '.join(cols)}) "
                    f"VALUES ({', '.join(['%s'] * (len(cols) + 1))}) RETURNING id",
                    (source_id, *row),
                )
                entry_id = cur.fetchone()[0]  # type: ignore[index]
                for alias_row in alias_rows.get(row[0], []):
                    cur.execute(
                        "INSERT INTO watchlist_aliases (entry_id, alias, alias_norm, alias_key,"
                        " alias_type, low_quality, script) VALUES (%s,%s,%s,%s,%s,%s,%s)"
                        " ON CONFLICT DO NOTHING",
                        (entry_id, *alias_row),
                    )
            cur.execute(
                "UPDATE watchlist_sources SET entry_count = %s WHERE id = %s",
                (len(entry_rows), source_id),
            )
        conn.commit()
    return {
        "entries": len(entry_rows),
        "aliases": sum(len(v) for v in alias_rows.values()),
        **{f"xml_{k}": v for k, v in xml_stats.items()},
    }


if __name__ == "__main__":
    from atlas.config import RAW_DIR

    print(load(RAW_DIR / "sanctions" / "ofac"))
