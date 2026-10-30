# CLAUDE.md — Urban Accessibility

Guidance for Claude Code working in this repo. Planning continues in Plan Mode from this spec;
anything marked **TBD-PLAN** is deliberately left for that session to decide.

## 1. Project in one paragraph

Score every Census **block group** in a target city 0–100 on **walking access to essential services**
(grocery, healthcare, parks, schools, transit), measured along the real walking network, and publish
the result as an interactive MapLibre choropleth (PMTiles on GitHub Pages) with a per-service breakdown
in popups. The story is **equity**: who has a 5-minute walk to a grocery store and who doesn't, and
which spatial patterns (street connectivity, land use, income, car ownership) drive that.

- First city: **Carson City, NV** — bbox `lon -119.8 → -119.7`, `lat 39.0 → 39.2`.
- Everything is driven by a city config file, so a new bbox = a new YAML file, no code changes.
- Repo: https://github.com/xoffvsg/urban-accessibility (Pages: https://xoffvsg.github.io/urban-accessibility/)

## 2. Decisions already made (do not reopen without asking)

| Topic | Decision |
|---|---|
| Shell / OS | VS Code **Remote-WSL**, WSL2 Ubuntu, bash. All commands are Linux commands. |
| Project location | `C:\dev\PortfolioProject4\Urban_Accessibility` = `/mnt/c/dev/PortfolioProject4/Urban_Accessibility` in WSL. Windows tools (QGIS, pgAdmin, Explorer) see the files directly. The conda env lives in WSL (`~/miniforge3`), not in the project folder. |
| Python | Dedicated conda env **`urban-access`** from `environment.yml` (conda-forge, Miniforge in WSL). Not `geoenv`, not a `.venv`. |
| Database | **Existing** Docker `kartoza/postgis` container, host port **5436** → 5432. New database `urban_access`; extensions `postgis`, `pgrouting`. Do not create a second container. |
| Analysis unit | **Census block groups** (~45–50 in Carson City). Tract GEOID kept as a parent attribute. |
| Routing | **pgRouting** on the Overture transportation network, inside PostGIS. |
| Scoring | **Tiered walk bands**: ≤400 m (5 min) = 1.0, ≤800 m (10 min) = 0.6, ≤1600 m (20 min) = 0.3, else 0. |
| Services | Grocery, healthcare, parks, schools, transit — all five in the composite. |
| Population | **Dasymetric**: block-group population allocated to residential buildings; access measured from buildings, aggregated population-weighted. |
| Census | **ACS 5-year** (latest vintage, pinned in config) — needs `CENSUS_API_KEY`. |
| Transit | City GTFS `stops.txt` if a URL is configured; otherwise Overture transit POIs. |
| Notebooks | Jupyter notebooks for development; reusable logic moves into `src/urban_access/`. |
| Web | MapLibre GL JS + `pmtiles` protocol, static files in `docs/`, deployed by GitHub Pages. |

## 3. Environment

See `SETUP.md` for the step-by-step bash setup. Summary:

```bash
conda env create -f environment.yml      # first time
conda activate urban-access
cp .env.example .env                     # fill PGUSER / PGPASSWORD / CENSUS_API_KEY
python -m ipykernel install --user --name urban-access
```

- Connection settings come from `.env` (`PGHOST=localhost`, `PGPORT=5436`, `PGDATABASE=urban_access`).
  Never hard-code credentials; never commit `.env`.
- **Loading `.env` in Python:** always pass an explicit path anchored at the repo root, e.g. in
  `src/urban_access/config.py`: `load_dotenv(Path(__file__).resolve().parents[2] / ".env")`.
  Bare `load_dotenv()` fails (`AssertionError` in `find_dotenv`) from stdin / heredoc scripts and can
  miss the file from notebooks whose cwd is `notebooks/`. Notebooks get settings via `config.py`, never
  by calling `load_dotenv()` themselves.
- Verified database facts (2026-10-29): kartoza container runs **PostgreSQL 18.6** (Debian build),
  role **`gis`**, reachable from WSL at `localhost:5436`; `psql` client 18 installed in Ubuntu.
  Prefer `psql` / psycopg over the port for all DB work — the WSL user may not have access to the
  Docker socket (`docker ps` → permission denied until the user is in the `docker` group).
- **Non-interactive `psql`:** always run `psql -P pager=off` (or set `PAGER=cat` in the command). The
  default pager stops at `(END)` and blocks the session waiting for `q`. Same for `git`: use `git --no-pager`.
- **pgRouting is 4.0.1** (`urban_access` has `postgis` + `pgrouting` enabled). v4 **removed**
  `pgr_createTopology`, `pgr_analyzeGraph`, `pgr_analyzeOneWay`, `pgr_createVerticesTable`, `pgr_nodeNetwork`
  and several older overloads (`pgr_withPoints*`, `pgr_trsp*`). Older tutorials using them will fail.
  Build topology from Overture connectors (segment split at connector positions → `source`/`target` =
  connector ids) and/or `pgr_extractVertices`; `pgr_separateCrossing` / `pgr_separateTouching` are
  available for fixing noding. Check signatures against https://docs.pgrouting.org/4.0/en/ before writing SQL.
- **DuckDB Python API (recent version in env):** relations from `con.sql(...)` no longer have
  `.fetchone()`. Use `con.sql(...).fetchall()`, `.df()`, `.arrow()`, or `con.execute(sql, params).fetchone()`.
  Prefer `con.execute(sql, params)` with bound parameters for bbox values from config.
- GDAL/ogr2ogr and tippecanoe come **from the conda env** (`which ogr2ogr` must point inside
  `.../envs/urban-access/`). Don't call Windows-side tools or the Anaconda3 base GDAL.
- tippecanoe ≥ 2.17 writes `.pmtiles` directly; no separate `pmtiles` CLI is required.
- pgAdmin / QGIS on Windows connect to `localhost:5436` and open project files at `C:\dev\PortfolioProject4\Urban_Accessibility\...`.
- The repo is on the Windows drive (`/mnt/c`), which is slower for many small files than the Linux
  filesystem. Fine at Carson City scale; if a large city makes I/O a bottleneck, `data_dir` in config
  may point to a Linux path (e.g. `~/data/urban_access`) — paths must always come from config.
- Line endings: `.gitattributes` forces LF; git in WSL uses `core.fileMode false` and `core.autocrlf input`.
  Use git from WSL only (not Windows git) on this repo to avoid mixed line endings.

## 4. Repository layout (target)

```
Urban_Accessibility/        # C:\dev\PortfolioProject4\Urban_Accessibility (GitHub repo: urban-accessibility)
├── CLAUDE.md               # this file
├── .gitattributes          # LF line endings
├── README.md               # portfolio case study (Phase 4) — follows reference/ template
├── SETUP.md                # environment setup in WSL bash
├── LICENSE                 # MIT (code); data licenses listed in README
├── environment.yml
├── .env.example            # copy to .env (git-ignored)
├── Makefile                # one target per pipeline step + `all`   (TBD-PLAN: exact targets)
├── config/
│   ├── scoring.yaml        # bands, weights, Overture category → service mapping (shared)
│   └── cities/
│       └── carson_city.yaml
├── notebooks/              # numbered, run in order; thin — import from src/
│   ├── 00_explore_categories.ipynb
│   ├── 01_extract_overture.ipynb
│   ├── 02_census.ipynb
│   ├── 03_load_postgis.ipynb
│   ├── 04_network.ipynb
│   ├── 05_access_scoring.ipynb
│   ├── 06_equity_analysis.ipynb
│   └── 07_export_web.ipynb
├── src/urban_access/
│   ├── config.py           # load + validate city/scoring YAML, derive UTM CRS, paths
│   ├── extract.py          # DuckDB → Overture S3 → GeoParquet
│   ├── census.py           # TIGER block groups + ACS API
│   ├── gtfs.py             # optional GTFS stops
│   ├── db.py               # SQLAlchemy/psycopg engine, run .sql files, ogr2ogr loader
│   ├── network.py          # build pgRouting graph, run distance queries
│   ├── scoring.py          # bands, per-service scores, composite
│   ├── export.py           # GeoJSON → tippecanoe → PMTiles, summary JSON
│   └── cli.py              # `python -m urban_access.cli <step> --city carson_city`
├── sql/                    # parameterised by schema name (= city slug)
├── tests/                  # pytest: config parsing, scoring math, band edges
├── data/                   # git-ignored except .gitkeep
│   ├── raw/<city_slug>/        # GeoParquet from Overture + Census + GTFS
│   ├── interim/<city_slug>/
│   └── processed/<city_slug>/  # final block-group table (GeoParquet + GeoJSON)
├── docs/                   # GitHub Pages root
│   ├── index.html
│   ├── app.js
│   ├── style.css
│   └── data/<city_slug>.pmtiles, <city_slug>_summary.json
├── images/headline.png     # README screenshot
└── reference/
```

`docs/data/*.pmtiles` **is** committed (it's the deployed product, a few MB). Everything under `data/` is not.

## 5. Configuration contract

`config/cities/<slug>.yaml` holds only city-specific values (bbox, Census vintage, GTFS URL,
Overture release). `config/scoring.yaml` holds method choices shared by all cities. Code must:

- Read the bbox from config only. Fetch Overture data for the bbox **expanded by `edge_buffer_m`**
  (default 2000 m) so services just outside the bbox still count; score only block groups whose
  **centroid** lies inside the bbox.
- Derive the analysis CRS from the bbox centroid (UTM zone; Carson City → EPSG:32611) when
  `analysis_crs: auto`. Store/analyse in that CRS; export web layers in EPSG:4326.
- Auto-detect state/county FIPS by intersecting the bbox with TIGER counties when set to `auto`
  (Carson City = 32 / 510). A bbox can span several counties — handle lists.
- Use one PostGIS **schema per city slug** (`carson_city.*`) so cities coexist in `urban_access`.
- Pin the Overture release string; record it and the ACS vintage in outputs and README.

## 6. Pipeline spec

### Phase 1 — Extract (DuckDB + S3) → `data/raw/<slug>/*.parquet`

DuckDB with `spatial` + `httpfs`, `s3_region='us-west-2'`, anonymous access, filter on the
`bbox.xmin/xmax/ymin/ymax` struct columns before touching geometry.
Path pattern: `s3://overturemaps-us-west-2/release/<release>/theme=<theme>/type=<type>/*`.

| Output file | Source | Notes |
|---|---|---|
| `places.parquet` | `places/place` | Keep id, names.primary, `basic_category`, `taxonomy` (primary, hierarchy, alternate), confidence, operating status if present, geometry. |
| `buildings.parquet` | `buildings/building` | id, class, subtype, height, num_floors, geometry. |
| `segments.parquet` | `transportation/segment` | subtype = road (+ any pedestrian subtypes), class, connectors, access restrictions, geometry. |
| `connectors.parquet` | `transportation/connector` | For network topology. |
| `parks.parquet` | `base/land_use` (park-like subtypes/classes) | Park **polygons**; supplements park POIs. TBD-PLAN: exact class filter. |
| `neighborhoods.parquet` | `divisions/division_area` (subtype neighborhood/microhood) | Optional: human-readable names for popups. |
| `block_groups.parquet` | TIGER/Line via `pygris` | Same vintage as ACS. |
| `acs.parquet` | Census API, ACS 5-yr, block group | See variable list below. |
| `gtfs_stops.parquet` | GTFS `stops.txt` | Only if `transit.gtfs_url` set. |

**Important — Overture schema change:** the `categories` column was removed from places in the
September 2026 release. Use `basic_category` and `taxonomy.primary` / `taxonomy.alternate`.
Notebook `00_explore_categories` must list the distinct values present in the bbox so the service
mapping in `scoring.yaml` is set from real data, not guessed. Places releases expire from S3 after
a while — cached raw files are the record; never assume an old release is still online.

ACS variables (verify codes against the pinned vintage):
total population `B01003_001E`; households with no vehicle `B25044_003E + B25044_010E` over
`B25044_001E`; median household income `B19013_001E`; population 65+ and under 18 from `B01001`.
Keep MOEs (`…M`) — block-group MOEs are large and must be shown or caveated.

Verify each export in the notebook: row count, CRS, bounds, null geometries, a quick plot.

### Phase 2 — Analysis (PostGIS + pgRouting)

1. **Load** each GeoParquet with `ogr2ogr` into `<slug>.<table>`, reprojected to the analysis CRS
   (`-t_srs`), `GEOMETRY_NAME=geom`, spatial index. Script it (`db.py` / `sql/`), idempotent (drop & reload).
2. **Walk network.** Keep segments a pedestrian can use: drop motorways/trunk-style classes and
   segments whose access restrictions deny foot travel. Build topology by splitting segments at
   their Overture connectors (or `pgr_extractVertices` approach; pgRouting 4 — no `pgr_createTopology`) → `edges(id, source, target, cost_m, geom)`,
   undirected, cost = length in metres. TBD-PLAN: whether to keep only the largest connected
   component; how to treat segments with no sidewalk data (Overture rarely has it — note as limitation).
3. **Snap** origins (residential buildings) and destinations (services) to nearest network vertex;
   store the snap distance and add it to the network distance. Flag snaps > 200 m.
4. **Distances.** For each service type, compute network distance from every origin vertex to the
   nearest service of that type, capped at 1600 m (multi-source `pgr_drivingDistance` with
   `equicost` or equivalent). Parks: use access points along the polygon boundary (polygon ∩
   network buffer) plus park POIs. Transit: stop points.
5. **Population allocation (dasymetric).** Residential buildings = Overture `class`/`subtype`
   residential, or unclassified buildings within a plausible footprint range not coincident with a
   non-residential POI (TBD-PLAN: thresholds). Weight = footprint area × floors (floors default 1).
   Allocate each block group's ACS population to its buildings by weight. Fallback when a block group
   has no qualifying buildings: regular point grid over the block group.
6. **Per block group metrics** (one row per block group):
   - `pop_total`, ACS equity fields (+MOE), tract GEOID, neighborhood name if available
   - for each service `s`: `<s>_score` (population-weighted mean of band value × 100),
     `<s>_pop_5min_pct` (share of residents ≤400 m), `<s>_n_400m` (count of services within 400 m
     network distance of any residence), `<s>_cover_400m_pct` (share of block-group area inside the
     400 m network service area), `<s>_dist_centroid_m` (network distance from centroid's nearest vertex)
   - `people_per_grocery` (population / groceries reachable within 1600 m)
   - `access_score` = Σ weight_s × `<s>_score` → 0–100
7. **Normalisation.** Scores are **absolute** (band-based, 0–100), not min–max rescaled, so cities are
   comparable. A city-relative rank/percentile column is added alongside.
8. **Sanity checks.** Compare network vs straight-line distance (circuity ratio) per block group;
   spot-check a few block groups by hand in QGIS; assert weights sum to 1.

Default weights (in `scoring.yaml`): grocery 0.25, healthcare 0.20, transit 0.20, schools 0.20, parks 0.15.

### Phase 2b — Equity analysis (`06_equity_analysis`)

Headline numbers: residents (and %) with/without a 5-minute walk to a grocery store; same for each
service. Break down by income tercile, zero-car-household share, 65+ share. Correlate
`access_score` with those fields; local spatial autocorrelation (LISA, `esda`) to show clusters of
low access. Every finding quoted in the README must come from a notebook cell.

### Phase 3 — Visualization (`docs/`)

- Export block groups (EPSG:4326, rounded coords, only popup fields) and service points to
  GeoJSON → `tippecanoe` → `docs/data/<slug>.pmtiles` (two layers: `block_groups`, `services`).
- `docs/data/<slug>_summary.json`: per-block-group scores for the chart + metadata (release, vintage, weights).
- `index.html`: MapLibre choropleth on `access_score` (sequential, colour-blind-safe ramp, fixed
  0–100 classes), popup with name, score, per-service breakdown, population, key ACS fields.
- Service filter: checkboxes/sliders re-weight services **client-side** from the per-service
  properties (set paint expression / feature-state); map, legend and chart update together.
- Sorted bar chart of block groups by score, linked to the map (hover/click highlights).
- Toggleable service-point layer by type. Basemap: free, no API key (TBD-PLAN: OpenFreeMap vs Protomaps).
- Attribution for Overture (ODbL/CDLA per theme), OSM-derived data, Census, basemap.
- Must work from `python -m http.server -d docs` and on GitHub Pages (range requests).

### Phase 4 — Portfolio

- `README.md` follows `reference/` section order; matter-of-fact
  voice, specific numbers, CRS stated, data licenses per dataset, "What I learned" written last
  and by me (Claude drafts only the factual sections unless asked).
- Screenshot `images/headline.png` (Playwright headless capture of the deployed page is fine).
- Reproduce in ≤3 commands: `conda env create …`, `make all CITY=carson_city`, `make serve`.

## 7. Working agreements for Claude Code

- **Ask before**: dropping a schema or database, changing `docker` state of the shared container,
  rewriting committed notebooks wholesale, adding a new dependency to `environment.yml`.
- The kartoza container is shared with other projects — only touch the `urban_access` database.
- Notebooks: numbered, run top-to-bottom, first cell loads config for `CITY` and sets paths;
  no hard-coded bbox, paths, or credentials. Move any function used twice into `src/`.
  Outputs are stripped on commit (`nbstripout`).
- SQL lives in `sql/*.sql`, parameterised by schema; no SQL strings longer than a few lines in Python.
- Units: metres in the analysis CRS everywhere. Name columns with units (`_m`, `_pct`).
- Style: `ruff` for lint/format; type hints in `src/`; `pytest` for scoring math and config parsing.
- Large files never go in git (raw data, intermediate GeoJSON). `docs/data/*.pmtiles` is the exception.
- Commit messages: imperative, one topic per commit.
- When a number in the README or a popup changes, regenerate it from the pipeline — never edit by hand.

## 8. Open items for Plan Mode (TBD-PLAN)

1. Final Overture `basic_category`/`taxonomy` → service mapping (from notebook 00 output).
2. Walkable segment filter and connected-component handling.
3. Residential-building heuristic thresholds; what to do with mobile-home parks / large lots.
4. JAC (Jump Around Carson) GTFS URL — operator `o-jumparoundcarson~nv~us` on Transitland.
5. Whether schools count all levels equally or only K-8 for walk access.
6. Basemap provider; colour ramp and class breaks.
7. Makefile targets and whether steps are notebook-driven (papermill) or CLI-driven.
8. Optional: H3 summary layer or a second city to prove the config is portable.
