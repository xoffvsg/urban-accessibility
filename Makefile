# Pipeline steps — reproduce with: make all CITY=carson_city
CITY ?= carson_city
PY   := python -m urban_access.cli

.PHONY: all phase1 census extract gtfs test lint serve

all: phase1

phase1:
	$(PY) phase1 --city $(CITY)

census:
	$(PY) census --city $(CITY)

extract: census
	$(PY) extract --city $(CITY)

gtfs:
	$(PY) gtfs --city $(CITY)

test:
	pytest -q

lint:
	ruff check src tests notebooks
	ruff format --check src tests

serve:
	cd docs && python -m RangeHTTPServer 8000
