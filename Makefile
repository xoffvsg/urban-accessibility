# Pipeline steps — reproduce with: make all CITY=carson_city
CITY ?= carson_city
PY   := python -m urban_access.cli

.PHONY: all phase1 census extract gtfs osm phase2 load network score equity export xcheck test lint serve

all: phase1 phase2 equity export

phase1:
	$(PY) phase1 --city $(CITY)

census:
	$(PY) census --city $(CITY)

extract: census
	$(PY) extract --city $(CITY)

gtfs:
	$(PY) gtfs --city $(CITY)

osm:
	$(PY) osm --city $(CITY)

phase2:
	$(PY) phase2 --city $(CITY)

load:
	$(PY) load --city $(CITY)

network:
	$(PY) network --city $(CITY)

score:
	$(PY) score --city $(CITY)

equity:
	$(PY) equity --city $(CITY)

export:
	$(PY) export --city $(CITY)

# Review tables against open data (not part of `all`); SOURCE=schools
SOURCE ?= schools
xcheck:
	$(PY) xcheck --city $(CITY) --source $(SOURCE)

test:
	pytest -q

lint:
	ruff check src tests notebooks
	ruff format --check src tests

serve:
	cd docs && python -m RangeHTTPServer 8000
