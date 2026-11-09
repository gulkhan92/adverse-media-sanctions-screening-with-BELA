# Data

Purpose: all datasets used by the screening platform, with scripts to rebuild them.
Raw downloads are git-ignored. Every source is registered in `manifest.yaml`
(name, URL, license, retrieval date, file size, SHA-256).

## Rebuild

```
make setup          # create .venv and install dependencies
make data-ofac      # primary sanctions watchlist (small, quick)
make data           # all downloads, then row counts and hash verification
make harvest-loop   # GDELT harvester, one cycle every 30 minutes
make verify-harvest # exit check: 48 hours without failure
make articles       # fetch article text, store snippets only
```

All download scripts are idempotent (a file whose hash matches the manifest is
skipped), resumable (HTTP Range on a `.part` file), checksum recorded, and log
JSON lines to stderr. Set `ATLAS_CONTACT` to a monitored address before running
network fetchers; it is added to the User-Agent header.

## Layout

| Path | Content |
|---|---|
| `raw/sanctions/ofac/` | SDN.CSV, ALT.CSV, ADD.CSV, SDN_COMMENTS.CSV, SDN_ADVANCED.XML, CONS_ADVANCED.XML |
| `raw/registry/gleif/` | Level 1 and Level 2 Golden Copy CSV |
| `raw/leaks/icij/` | nodes-*.csv, relationships.csv |
| `raw/eval/mewsli_x/` | Mewsli-X snapshot and common JSONL conversion |
| `raw/news/gdelt/` | Daily harvest JSONL (metadata) and `_status.json` |
| `processed/` | Parquet and JSONL outputs (Wikidata subset, article snippets) |
| `gold/` | Hand-labeled evaluation set (committed) |
| `annotation/` | Label Studio configuration and annotation guidelines |
| `reports/` | Row count report |
| `synthetic/`, `seed/` | Reserved for phases 2 and 6 (business data and demo seed) |

Not downloaded in phase 1: OpenSanctions (optional demo source) and the Multilingual
CC-News slices (`raw/news/cc_news/`), which are only needed for load tests in later
phases. The Wikidata subset is built by `build_wikidata_subset.py` from a dump you
supply with `--input`.

## Licensing register

| Source | License | Attribution | Redistribution |
|---|---|---|---|
| OFAC SDN and Consolidated lists | US federal government work, public domain | U.S. Department of the Treasury, OFAC | Allowed |
| OpenSanctions (optional, not downloaded) | CC BY-NC 4.0 | OpenSanctions | Non-commercial demo only, disabled by default |
| GLEIF Golden Copy | GLEIF LEI data terms of use | GLEIF | Follow GLEIF terms |
| ICIJ Offshore Leaks Database | Database ODbL, contents CC BY-SA | ICIJ Offshore Leaks Database | Keep derived tables separate, do not redistribute. UI must show: inclusion does not imply improper conduct, and many people share names |
| Wikidata | CC0 | Wikidata contributors | Allowed |
| GDELT DOC 2.0 | Free for research use | GDELT Project | Metadata only, publishers keep copyright |
| Article text (fetched) | Publisher copyright | Original publisher | Store snippets of at most 300 characters, link to source |
| Mewsli-X | Apache-2.0 per dataset card | Dataset authors | Evaluation use |
| Gold set | Own work | This project | Committed |

## Data handling rules

- Full article bodies are never written to disk or redisplayed.
- The fetcher obeys robots.txt, limits request rate per domain, and blocks private
  address ranges.
- OpenSanctions data, if ever used, stays in a separate schema behind a feature flag.
