"""Harvest news article metadata from the GDELT DOC 2.0 API.

Runs one query per (language, topic) pair: 7 languages by 7 topic groups. Results
are appended to data/raw/news/gdelt/YYYY-MM-DD.jsonl (one JSON object per line,
deduplicated by URL per day). Only metadata is stored; publishers keep copyright.

Usage:
  python harvest_gdelt.py                  one full cycle
  python harvest_gdelt.py --loop 30        repeat every 30 minutes
  python harvest_gdelt.py --lang english --topic fraud   single query (testing)

Each cycle writes data/raw/news/gdelt/_status.json for the 48 hour health check
in verify_harvest.py.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from common import RAW_DIR, USER_AGENT, get_logger

API = "https://api.gdeltproject.org/api/v2/doc/doc"
OUT_DIR = RAW_DIR / "news" / "gdelt"
STATUS_PATH = OUT_DIR / "_status.json"
MIN_INTERVAL_SECONDS = 5.5  # documented limit is about one request per 5 seconds
MAX_RECORDS = 250

TOPICS = (
    "fraud",
    "corruption",
    "bribery",
    "money_laundering",
    "sanctions",
    "embezzlement",
    "terrorism_financing",
)

# GDELT source language name -> ISO 639-1 code -> one query phrase per topic.
LANGUAGES: dict[str, tuple[str, dict[str, str]]] = {
    "english": ("en", dict(zip(TOPICS, [
        "fraud", "corruption", "bribery", '"money laundering"', "sanctions",
        "embezzlement", '"terrorism financing"'], strict=True))),
    "spanish": ("es", dict(zip(TOPICS, [
        "fraude", "corrupción", "soborno", '"lavado de dinero"', "sanciones",
        "malversación", '"financiación del terrorismo"'], strict=True))),
    "french": ("fr", dict(zip(TOPICS, [
        "fraude", "corruption", '"pots-de-vin"', '"blanchiment d\'argent"', "sanctions",
        '"détournement de fonds"', '"financement du terrorisme"'], strict=True))),
    "german": ("de", dict(zip(TOPICS, [
        "Betrug", "Korruption", "Bestechung", "Geldwäsche", "Sanktionen",
        "Veruntreuung", "Terrorismusfinanzierung"], strict=True))),
    "arabic": ("ar", dict(zip(TOPICS, [
        "احتيال", "فساد", "رشوة", '"غسيل الأموال"', "عقوبات",
        "اختلاس", '"تمويل الإرهاب"'], strict=True))),
    "russian": ("ru", dict(zip(TOPICS, [
        "мошенничество", "коррупция", "взяточничество", '"отмывание денег"', "санкции",
        "растрата", '"финансирование терроризма"'], strict=True))),
    "turkish": ("tr", dict(zip(TOPICS, [
        "dolandırıcılık", "yolsuzluk", "rüşvet", '"kara para aklama"', "yaptırımlar",
        "zimmet", '"terörün finansmanı"'], strict=True))),
}


def build_query(language: str, topic: str) -> str:
    _, phrases = LANGUAGES[language]
    return f"{phrases[topic]} sourcelang:{language}"


def parse_seendate(value: str | None) -> datetime:
    """GDELT timestamps look like 20261109T093000Z."""
    if value:
        try:
            return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        except ValueError:
            pass
    return datetime.now(UTC)


def normalize(article: dict[str, Any], language: str, topic: str) -> dict[str, Any]:
    return {
        "url": article.get("url"),
        "title": article.get("title"),
        "domain": article.get("domain"),
        "language": LANGUAGES[language][0],
        "source_country": article.get("sourcecountry"),
        "seen_at": parse_seendate(article.get("seendate")).isoformat(),
        "image": article.get("socialimage"),
        "topic": topic,
        "harvested_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def existing_urls(path: Path) -> set[str]:
    if not path.exists():
        return set()
    with path.open(encoding="utf-8") as handle:
        return {json.loads(line)["url"] for line in handle if line.strip()}


def append_day_file(rows: list[dict[str, Any]]) -> int:
    """Append rows to the file for each row's seen date, skipping known URLs."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    by_day: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_day.setdefault(row["seen_at"][:10], []).append(row)
    written = 0
    for day, day_rows in by_day.items():
        path = OUT_DIR / f"{day}.jsonl"
        known = existing_urls(path)
        with path.open("a", encoding="utf-8") as handle:
            for row in day_rows:
                if not row["url"] or row["url"] in known:
                    continue
                known.add(row["url"])
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                written += 1
    return written


def query_api(client: httpx.Client, query: str, timespan: str) -> list[dict[str, Any]]:
    params = {
        "query": query,
        "mode": "ArtList",
        "format": "json",
        "maxrecords": str(MAX_RECORDS),
        "timespan": timespan,
        "sort": "datedesc",
    }
    for attempt in range(5):
        response = client.get(API, params=params)
        if response.status_code == 429:
            time.sleep(10 * (attempt + 1))
            continue
        response.raise_for_status()
        if not response.text.strip().startswith("{"):
            return []  # GDELT returns plain text for empty or invalid queries
        return list(response.json().get("articles", []))
    raise RuntimeError("rate limited after 5 attempts")


def run_cycle(
    languages: list[str], topics: list[str], timespan: str
) -> dict[str, Any]:
    log = get_logger("gdelt")
    started = datetime.now(UTC)
    total_new = errors = queries = 0
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60) as client:
        for language in languages:
            for topic in topics:
                queries += 1
                try:
                    articles = query_api(client, build_query(language, topic), timespan)
                    rows = [normalize(a, language, topic) for a in articles]
                    new = append_day_file(rows)
                    total_new += new
                    log.info(f"{language}/{topic}: fetched={len(rows)} new={new}")
                except (httpx.HTTPError, RuntimeError, ValueError) as exc:
                    errors += 1
                    log.error(f"{language}/{topic}: {exc}")
                time.sleep(MIN_INTERVAL_SECONDS)
    status = {
        "started": started.isoformat(timespec="seconds"),
        "finished": datetime.now(UTC).isoformat(timespec="seconds"),
        "queries": queries,
        "errors": errors,
        "new_articles": total_new,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    history = json.loads(STATUS_PATH.read_text()) if STATUS_PATH.exists() else []
    history.append(status)
    STATUS_PATH.write_text(json.dumps(history[-2000:], indent=1))
    return status


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--loop", type=float, default=0, help="repeat every N minutes")
    parser.add_argument("--lang", choices=list(LANGUAGES), help="restrict to one language")
    parser.add_argument("--topic", choices=TOPICS, help="restrict to one topic")
    parser.add_argument("--timespan", default="1d", help="GDELT timespan, for example 1d or 6h")
    args = parser.parse_args()
    languages = [args.lang] if args.lang else list(LANGUAGES)
    topics = [args.topic] if args.topic else list(TOPICS)
    log = get_logger("gdelt")
    while True:
        status = run_cycle(languages, topics, args.timespan)
        log.info(f"cycle complete: {json.dumps(status)}")
        if not args.loop:
            break
        time.sleep(max(0.0, args.loop * 60))


if __name__ == "__main__":
    main()
