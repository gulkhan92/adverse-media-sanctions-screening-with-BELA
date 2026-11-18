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

# Phase 3: models, services, evaluation ---------------------------------------------
.PHONY: models models-all llm-up nel-up blink-up eval throughput
models:
	$(PY) scripts/fetch_models.py bela-small
	$(PY) scripts/fetch_models.py blink
	$(PY) scripts/fetch_models.py llm
models-all: models
	$(PY) scripts/fetch_models.py bela-index
llm-up:
	services/llm/run_server.sh
nel-up:
	PYTHONPATH=services/nel:apps/api .venv/bin/uvicorn atlas_nel.app:app --host 127.0.0.1 --port 8082
blink-up:
	PYTHONPATH=services/blink .venv/bin/uvicorn atlas_blink.app:app --host 127.0.0.1 --port 8083
eval:
	$(PY) scripts/run_eval.py
throughput:
	$(PY) scripts/throughput_test.py

# Phase 4: backend API ----------------------------------------------------------------
.PHONY: api-up valkey-up api-test api-security audit-verify api-latency
valkey-up:
	valkey-server --port 6379 --save '' --appendonly no
api-up:
	.venv/bin/uvicorn --factory atlas.api.app:factory --app-dir apps/api --host 127.0.0.1 --port 8000
api-test:
	.venv/bin/pytest -q tests/api/test_api_*.py tests/api/test_phase5_*.py tests/api/test_phase6_*.py \
		--cov=atlas.api --cov=atlas.jobs --cov-report=term-missing
api-security:
	.venv/bin/bandit -q -r apps/api/atlas/api -ll
	.venv/bin/pip-audit
audit-verify:
	$(PY) -m atlas.api.cli verify-audit
api-latency:
	$(PY) scripts/api_latency.py --email $(EMAIL) --password $(PASSWORD)

# Phase 5: workers and scheduled jobs ---------------------------------------------------
.PHONY: worker beat monitor-now
BROKER ?= redis://127.0.0.1:6379/1
worker:
	ATLAS_CELERY_BROKER_URL=$(BROKER) .venv/bin/celery -A atlas.jobs.celery_app worker \
		-Q match,ingest,nlp,reports -l info
beat:
	ATLAS_CELERY_BROKER_URL=$(BROKER) .venv/bin/celery -A atlas.jobs.celery_app beat -l info
monitor-now:
	ATLAS_CELERY_BROKER_URL=$(BROKER) .venv/bin/celery -A atlas.jobs.celery_app call atlas.monitor_scan

# Phase 6: synthetic business data and analytics views ---------------------------------
.PHONY: business-data business-load business-refresh business
business-data:
	$(PY) $(SCRIPTS)/generate_demo_business_data.py
business-load:
	$(PY) -m atlas.etl.business
business-refresh:
	infra/dev_db.sh psql -c "SELECT refresh_business_views()"
business: business-data business-load
