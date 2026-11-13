PY ?= .venv/bin/python
SCRIPTS = data/scripts
export PYTHONPATH := apps/api

.PHONY: db-up db-down migrate etl etl-wikidata-fetch etl-ofac etl-wikidata etl-gleif etl-icij \
	etl-articles embed resolve report setup data data-ofac data-gleif data-icij data-mewsli harvest harvest-loop \
	counts verify-data verify-harvest articles test lint check-chars

setup:
	python3 -m venv .venv
	.venv/bin/pip install -e '.[dev]'

# Download all raw sources, record hashes in data/manifest.yaml, print row counts.
# GLEIF and ICIJ are large; run `make data-ofac` alone for a quick start.
data: data-ofac data-gleif data-icij data-mewsli counts verify-data

data-ofac:
	$(PY) $(SCRIPTS)/download_ofac.py
data-gleif:
	$(PY) $(SCRIPTS)/download_gleif.py
data-icij:
	$(PY) $(SCRIPTS)/download_icij.py
data-mewsli:
	$(PY) $(SCRIPTS)/download_mewsli_x.py

harvest:
	$(PY) $(SCRIPTS)/harvest_gdelt.py
harvest-loop:
	$(PY) $(SCRIPTS)/harvest_gdelt.py --loop 30
verify-harvest:
	$(PY) $(SCRIPTS)/verify_harvest.py --hours 48

articles:
	$(PY) $(SCRIPTS)/fetch_article_text.py --limit 200

counts:
	$(PY) $(SCRIPTS)/report_counts.py
verify-data:
	$(PY) $(SCRIPTS)/verify_manifest.py

test:
	.venv/bin/pytest -q
lint:
	.venv/bin/ruff check data/scripts apps tests scripts
check-chars:
	@python3 scripts/check_forbidden_chars.py

# Phase 2: database, ETL, embeddings, resolution, quality report ---------------------
db-up:
	infra/dev_db.sh start
db-down:
	infra/dev_db.sh stop
migrate:
	cd apps/api && ../../.venv/bin/alembic upgrade head

etl-wikidata-fetch:
	$(PY) $(SCRIPTS)/fetch_wikidata_entities.py countries
	$(PY) $(SCRIPTS)/fetch_wikidata_entities.py leis
	$(PY) $(SCRIPTS)/fetch_wikidata_entities.py search
	$(PY) $(SCRIPTS)/fetch_wikidata_entities.py fetch
etl-ofac:
	$(PY) -m atlas.etl.ofac
etl-wikidata:
	$(PY) -m atlas.etl.wikidata
etl-gleif:
	$(PY) -m atlas.etl.gleif
etl-icij:
	$(PY) -m atlas.etl.icij
etl-articles:
	$(PY) -m atlas.etl.gdelt
# Order matters: OFAC and Wikidata first, GLEIF merges into Wikidata entities by LEI.
etl: etl-ofac etl-wikidata etl-gleif etl-icij etl-articles

embed:
	$(PY) -m atlas.embeddings
resolve:
	$(PY) -m atlas.resolution_cli
report:
	$(PY) -m atlas.report
