PY ?= .venv/bin/python
SCRIPTS = data/scripts

.PHONY: setup data data-ofac data-gleif data-icij data-mewsli harvest harvest-loop \
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
	.venv/bin/ruff check data/scripts tests
check-chars:
	@python3 scripts/check_forbidden_chars.py
