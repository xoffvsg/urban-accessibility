# Pipeline steps — reproduce with: make all CITY=carson_city
CITY ?= carson_city
PY   := python -m urban_access.cli

.PHONY: all phase1 census extract gtfs phase2 load network score test lint serve

all: phase1 phase2

phase1:
	$(PY) phase1 --city $(CITY)

census:
	$(PY) census --city $(CITY)

extract: census
	$(PY) extract --city $(CITY)

gtfs:
	$(PY) gtfs --city $(CITY)

phase2:
	$(PY) phase2 --city $(CITY)

load:
	$(PY) load --city $(CITY)

network:
	$(PY) network --city $(CITY)

score:
	$(PY) score --city $(CITY)

test:
	pytest -q

lint:
	ruff check src tests notebooks
	ruff format --check src tests

serve:
	cd docs && python -m RangeHTTPServer 8000
