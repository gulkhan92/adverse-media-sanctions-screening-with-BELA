"""Entity titles, descriptions, and coarse kind for candidate QIDs, with a JSONL cache.

Cross-encoder and LLM adjudication need English text for each candidate. Information is read
from the cache and fetched from the Wikidata API (50 ids per request, polite delay) only for
missing QIDs. ``kind`` is ``person`` for instances of human (Q5), ``organization`` when the
item carries organization-type properties, and ``other`` otherwise (the type filter drops it).
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx

API = "https://www.wikidata.org/w/api.php"
ORG_PROPERTIES = ("P1278", "P159", "P452", "P112", "P740", "P355", "P749", "P127", "P571")
USER_AGENT = (
    "AtlasScreeningResearchBot/0.1 "
    "(contact: https://github.com/gulkhan92/adverse-media-sanctions-screening-with-BELA)"
)


@dataclass(frozen=True)
class EntityInfo:
    qid: str
    title: str
    description: str
    kind: str  # person, organization, other
    missing: bool = False
    labels: dict[str, str] = field(default_factory=dict)
    aliases: dict[str, list[str]] = field(default_factory=dict)


def kind_of(entity: dict[str, Any]) -> str:
    claims = entity.get("claims", {})
    for claim in claims.get("P31", []):
        value = claim.get("mainsnak", {}).get("datavalue", {}).get("value", {})
        if isinstance(value, dict) and value.get("id") == "Q5":
            return "person"
    if any(prop in claims for prop in ORG_PROPERTIES):
        return "organization"
    return "other"


class EntityInfoStore:
    def __init__(
        self, path: Path, delay: float = 0.6, languages: tuple[str, ...] = ("en",)
    ) -> None:
        self.path = path
        self.delay = delay
        self.languages = languages
        self.items: dict[str, EntityInfo] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                record = json.loads(line)
                self.items[record["qid"]] = EntityInfo(**record)

    def get(self, qid: str) -> EntityInfo:
        return self.items.get(qid) or EntityInfo(qid, qid, "", "other", missing=True)

    def ensure(self, qids: list[str]) -> int:
        """Fetch information for QIDs not yet cached. Returns the number fetched."""
        todo = sorted({q for q in qids if q not in self.items})
        if not todo:
            return 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with (
            httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60) as http,
            self.path.open("a", encoding="utf-8") as sink,
        ):
            for i in range(0, len(todo), 50):
                batch = todo[i : i + 50]
                entities = self._request(http, batch)
                for qid in batch:
                    entity = entities.get(qid)
                    if entity is None or "missing" in entity:
                        info = EntityInfo(qid, qid, "", "other", missing=True)
                    else:
                        info = EntityInfo(
                            qid,
                            entity.get("labels", {}).get("en", {}).get("value", qid),
                            entity.get("descriptions", {}).get("en", {}).get("value", ""),
                            kind_of(entity),
                            labels={lg: v["value"] for lg, v in entity.get("labels", {}).items()},
                            aliases={
                                lg: [a["value"] for a in values]
                                for lg, values in entity.get("aliases", {}).items()
                            },
                        )
                    self.items[qid] = info
                    sink.write(json.dumps(asdict(info), ensure_ascii=False) + "\n")
                sink.flush()
                time.sleep(self.delay)
        return len(todo)

    def _request(self, http: httpx.Client, batch: list[str]) -> dict[str, Any]:
        params = {
            "action": "wbgetentities",
            "ids": "|".join(batch),
            "format": "json",
            "props": "labels|descriptions|claims|aliases",
            "languages": "|".join(self.languages),
        }
        for attempt in range(8):
            response = http.get(API, params=params)
            if response.status_code in (429, 502, 503, 504):
                time.sleep(min(60, 2 ** (attempt + 1)))
                continue
            response.raise_for_status()
            return dict(response.json().get("entities", {}))
        raise RuntimeError("Wikidata API unavailable")
