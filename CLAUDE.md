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
- **Check the environment at session start:** run `which python` once. It must be
  `~/miniforge3/envs/urban-access/bin/python`. If it isn't (Claude Code was started from `(base)`),
  tell the user to `/exit`, `conda activate urban-access` and restart, or prefix commands with
  `conda run -n urban-access …`. Never install packages into `base` to work around it.
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
  **Don't use multi-source `pgr_drivingDistance(..., equicost := true)` for "nearest service":**
  in 4.0.1 it returned 506 m for a vertex whose nearest stop is 453 m away. Use a virtual
  super-source joined to every destination at cost 0 and a single-source `pgr_drivingDistance`
  (`sql/50_distances.sql`); `network.check_pairs` re-checks against one-to-many `pgr_dijkstraCost`.
- **pgRouting cost scales with the number of sources, not destinations reached.** Plain multi-source
  `pgr_drivingDistance` runs one Dijkstra per start vertex. When only "is group X within r?" matters
  (a park reached at any of its boundary points), give each group one virtual source with **one-way**
  zero-cost links to its points (`reverse_cost = -1`, `directed := true`, street edges with
  `reverse_cost = cost`). Undirected zero-cost links would let routes enter another group's node and
  cross that park for free. Also: a pipeline distance above the last band (1,600 m) is an **upper
  bound** (searches stop there), so `check_pairs` comparisons are exact only within it.
- **DuckDB Python API (recent version in env):** relations from `con.sql(...)` no longer have
  `.fetchone()`. Use `con.sql(...).fetchall()`, `.df()`, `.arrow()`, or `con.execute(sql, params).fetchone()`.
  Prefer `con.execute(sql, params)` with bound parameters for bbox values from config.
- **`PROJ_DATA`:** if Claude Code / VS Code starts a process with the env's `bin/` on PATH but without
  `conda activate` (`CONDA_PREFIX` still points at base), `PROJ_DATA` is unset and pyogrio/GDAL print
  `PROJ: proj_create_from_database: Open of …/share/proj failed`. `urban_access/__init__.py` sets it
  from `sys.prefix`; in notebooks, `import urban_access` before geopandas.
- GDAL/ogr2ogr and tippecanoe come **from the conda env** (`which ogr2ogr` must point inside
  `.../envs/urban-access/`). Don't call Windows-side tools or the Anaconda3 base GDAL.
- **GDAL drivers are plugins on conda-forge.** `gdal` pulls only `libgdal-core`, which has no Parquet and
  no PostgreSQL driver. `environment.yml` adds `libgdal-arrow-parquet` and `libgdal-pg` (note: the
  `libgdal` meta-package does *not* include the Parquet plugin). Before any `ogr2ogr` load, check
  `ogr2ogr --formats | grep -E "Parquet|PostgreSQL"` prints both; if not, ask the user to run
  `conda env update -f environment.yml --prune`. Never fall back to another GDAL install.
- tippecanoe ≥ 2.17 writes `.pmtiles` directly; no separate `pmtiles` CLI is required. Keep `-r1` in
  `export.write_tiles`: without it, tippecanoe drops most service points at low zooms.
- Browser testing: Playwright (conda `playwright` + `nodejs`) with headless Chromium in
  `~/.cache/ms-playwright` (`playwright install chromium`; Ubuntu needed `sudo apt-get install libnss3
  libnspr4 libasound2t64`). Node finds the library only with
  `NODE_PATH=~/miniforge3/envs/urban-access/lib/node_modules`. `scripts/check_map.js` checks the page
  served by `make serve` (or `PORT=…`, or the live site with `URL=…`) and writes screenshots; run
  Claude's checks on port 8010 so the user's `make serve` on 8000 is left alone.
  `scripts/screenshot.js` writes the README image `images/headline.png` (from `URL` or a local port);
  `python scripts/readme_table.py` prints the README Findings top/bottom table.
- Local map preview: `rangehttpserver` (pip, in `environment.yml`) → `cd docs && python -m RangeHTTPServer 8000`.
  It has no `--directory` option, so it must run from inside `docs/`. The web page reads only static files
  (PMTiles + JSON); no database driver or app server is involved at view time.
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
├── README.md               # portfolio case study (Phase 4)
├── SETUP.md                # environment setup in WSL bash
├── LICENSE                 # MIT (code); data licenses listed in README
├── environment.yml
├── .env.example            # copy to .env (git-ignored)
├── Makefile                # census / extract / gtfs / phase1 / test / lint / serve; `all` grows per phase
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
│   ├── nces.py / opendata.py / arcgis.py # NCES, SNAP, NPPES, HRSA, city parks (cross-check data)
│   ├── xcheck.py           # cross-checks vs open data → data/interim/<slug>/xcheck_<source>.csv
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
└── images/headline.png     # README screenshot
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
| `places.parquet` | `places/place` | id, name, `basic_category`, `taxonomy_primary` / `taxonomy_hierarchy` / `taxonomy_alternates` (flattened), brand, confidence, operating_status, geometry. |
| `buildings.parquet` | `buildings/building` | id, class, subtype, height, num_floors, geometry. |
| `segments.parquet` | `transportation/segment` | subtype = road (+ any pedestrian subtypes), class, connectors, access restrictions, geometry. |
| `connectors.parquet` | `transportation/connector` | For network topology. |
| `parks.parquet` | `base/land_use` (park-like subtypes/classes) | Park **polygons**; supplements park POIs. Filter = `services.parks.landuse` in `scoring.yaml` (park: park, dog_park; recreation: playground, recreation_ground). |
| `neighborhoods.parquet` | `divisions/division_area` (subtype neighborhood/microhood) | Optional: human-readable names for popups. |
| `block_groups.parquet` | TIGER/Line via `pygris` | Same vintage as ACS. |
| `acs.parquet` | Census API, ACS 5-yr, block group | See variable list below. |
| `gtfs_stops.parquet` | GTFS `stops.txt` | Only if `transit.gtfs_url` set. |
| `osm_gates.parquet`, `osm_fences.parquet` | OSM via Overpass (`osm.py`, `make osm`) | Overture has no barriers. Gate points (`closed` = access=no / foot=no, not overridden by foot=yes) and fence/wall lines; live data, timestamp in `.meta.json`; ODbL. |
| `nces_schools.parquet` | NCES EDGE REST (public: CCD locations + admin data, grade span, status) + PSS CSV (private; older year fills survey non-respondents) (`nces.py`, `make xcheck`) | Cross-check only, never a service source. `xcheck.py` pairs places by name + distance → `data/interim/<slug>/xcheck_schools.csv` (`suggestion` add/drop/review/missing); decisions go into `include_names` / `place_overrides`. Public domain. |
| `snap_retailers.parquet`, `nppes_orgs.parquet`, `hrsa_sites.parquet`, `city_parks.parquet` | USDA SNAP (live ArcGIS layer), CMS NPI Registry API (NPI-2 by `xcheck.nppes_postal_codes`, geocoded with the Census batch geocoder), HRSA sites CSV, city parks ArcGIS layer (`xcheck.city_parks_url`) (`opendata.py`, `arcgis.py`, `make xcheck SOURCE=all`) | Cross-checks → `xcheck_<source>.csv`. Grocery full-service = SNAP Supermarket / Super Store. Healthcare is site-based, not one-to-one: a place is essential when a name-matched NPI within `confirm_m` has an essential taxonomy prefix (`scoring.yaml`). `city_parks` is also an **input**: with `extra_parks: true`, PARK/SPORT polygons overlapping no Overture park are added to `parks` (`db.build_parks`); licence not stated, attributed. `place_overrides` `verified: true` = confirmed by a source (confidence 1.0). |
| `parcels.parquet` | City config `homes:` (`opendata.homes`, `make homes`, part of `phase1`): Carson City GIS Existing Land Use (`Total_DWUn`, `LU_Categor`); Douglas County GIS parcels (OBJECTID + geometry) + zoning (`ZoningCode`) | **Input** for homes (Phase 2 step 5). Columns `parcel_id, county, units, rule, category`. Owner/address fields never requested; no licence stated, attributed; never committed. |

**Important — Overture schema change:** the `categories` column was removed from places in the
September 2026 release. Use `basic_category` and `taxonomy.primary` / `taxonomy.hierarchy` / `taxonomy.alternates`
(plural). Service matching uses `taxonomy.hierarchy` + `alternates`; `basic_category` is too coarse.
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
2. **Walk network** (`sql/20_network.sql`). Every Overture road segment except `class = motorway`
   (trunk = urban Carson/William St, kept), and except segments with a foot denial (`denied`, no
   `heading`, mode absent or `foot`) that no rule allows (`allowed`/`designated` for foot/all modes;
   `as_private` counts as allowed). Rules with `between` apply to the whole segment (limitation).
   Segments listed in the city's `blocked_segments:` (field-checked fences/gates; Overture has no
   barriers) are dropped too (`walk_segments.blocked_note`). Gated areas (`gated_areas:` → GeoJSON in
   `config/cities/`, editable in QGIS): edges crossing the boundary are removed except the listed exit
   streets, inside edges get `edges.gated`, and origins/destinations snap only to edges on their own side.
   Gates (`gates:` → GeoJSON lines across each gate): `closed` removes the edges a line crosses;
   `open` splits the nearest remaining edge at each end and adds the line as an edge (`class = gate`),
   after the gated-area cut, so it is an exit. OSM gates with `closed` remove the edge they lie on
   (not at a junction). Untagged OSM gates are reference only (the user fixes cases as found).
   Topology: split each segment at its connectors (`ST_LineSubstring` between consecutive `at`) →
   `edges(id, source, target, cost_m, …)`, undirected, connector UUIDs → bigint `vertices`. Only the
   **largest connected component** (`edges.main`, 98.8 % of length here) is used. Roads without a
   mapped sidewalk are walkable (Overture has 2,165 sidewalk / 1,685 crosswalk segments; limitation).
3. **Snap** origins and destinations to the **nearest main edge** (not vertex: blocks are 100–200 m):
   `edge_id`, `frac`, `snap_m`. Flag snaps > 200 m. Destinations become virtual nodes with two edges
   to their edge's ends (cost = snap + along-edge part).
4. **Distances** (`sql/50_distances.sql`). Per service: super-source → all destination nodes (cost 0),
   single-source `pgr_drivingDistance` to 1600 m → nearest-destination distance per vertex. Origin
   distance = `snap_m + min(d(source) + frac·len, d(target) + (1−frac)·len)`, or direct along the edge
   when a destination shares it. Parks (Overture land use + `extra_parks` city polygons): boundary points every 25 m within 30 m of a main edge, plus
   park POIs; `group_key` = one park. Transit: GTFS stops. Per-destination 400 m reach gives the
   `n_400m` counts: **one search per `group_key`**, from a virtual node with one-way zero-cost links
   to the group's access points (`group_src` / `group_links`, directed search), not one per access
   point (parks: 117 searches instead of ~4,300).
5. **Population allocation (dasymetric)** (`sql/30_origins.sql`). Homes come from **parcel dwelling
   units** where the city config has a `homes:` source (`opendata.homes`, `make homes` → `parcels`):
   Carson City assessor `Total_DWUn`; Douglas County one home per parcel in residential zones (largest
   building), MFR by floor area / `m2_per_unit`, none in commercial/industrial/public zones. Units go
   to eligible buildings in the parcel (point-on-surface, ≥ 40 m², subtype in `parcel_subtypes`, class
   not in `parcel_exclude_classes`): 1 unit → largest building, more → by area × floors; assessor
   units with no building → a parcel point. Counties without a source keep the **footprint
   heuristic** (`res_heuristic`, always built for comparison): Overture residential classes (incl.
   `static_caravan`; not garages) plus unclassified footprints of 40–1,500 m² with no open
   non-residential place inside, weight = area × floors (floors = `num_floors`, else
   `round(height / 3.5)`, min 1). Each BG's ACS population is spread by weight (building → BG by
   `ST_PointOnSurface`). Fallback: 50 m point grid for a populated BG with no homes. Settings in
   `scoring.yaml` `residential:`; review `xcheck_homes.csv` (units vs ACS households). Parcel layers
   also hold owner names: request only the configured fields. Band scoring and aggregation run in
   Python (`scoring.py`, tested), not SQL.
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
service. Every finding quoted in the README must come from a notebook cell. Method (decided
2026-10-30, `src/urban_access/equity.py`, `make equity`):
- Groups are **count-weighted**, not terciles of a share: each block group's resident band shares
  weighted by its count of zero-car households (B25044), residents 65+ (B01001) or residents below
  `equity.poverty_ratio` × poverty line (C17002, default 2.0). Ecological caveat: members assumed spread
  over the block group's homes like all residents.
- Income: also thirds of residents by block-group median income (`weighted_terciles`).
- MOE: Monte Carlo (`equity.mc_draws`, fixed `seed`), counts and income ~ Normal(est, MOE/1.645),
  clipped ≥ 0, independent. Report point + p05–p95; the draws attenuate correlations and mix
  terciles, so ranges are robustness checks and the point can sit at their edge.
- Spearman ρ across populated block groups; Queen contiguity LISA (`esda`, 9,999 permutations), also
  FDR-adjusted. Outputs: `data/processed/<slug>/equity_summary.json`, `equity_groups.csv`,
  `block_groups_equity.parquet` and `<slug>.bg_equity` (`lisa_cluster`, `income_tercile`) for Phase 3.

### Phase 3 — Visualization (`docs/`)

- Export block groups (EPSG:4326, rounded coords, only popup fields) and service points to
  GeoJSON → `tippecanoe` → `docs/data/<slug>.pmtiles` (layers `block_groups`, `services`, `parks`;
  `src/urban_access/export.py`, `make export`). Park entrance points (`landuse_access`) are not exported.
- `docs/data/<slug>_summary.json`: per-block-group scores for the chart + metadata (release, vintage, weights).
- `index.html`: MapLibre choropleth on `access_score` (sequential, colour-blind-safe ramp, fixed
  0–100 classes), popup with name, score, per-service breakdown, population, key ACS fields.
- Service filter: checkboxes/sliders re-weight services **client-side** from the per-service
  Weights are whole percentages summing to 100: a slider changes only the sliders below it (in
  proportion), the last enabled one takes the remainder; unchecking rescales the rest to 100.
  properties (set paint expression / feature-state); map, legend and chart update together.
- Sorted bar chart of block groups by score, linked to the map (hover/click highlights).
- Toggleable service-point layer by type; optional LISA cluster outlines (default weights). Basemap:
  OpenFreeMap positron. Service points are drawn from `service_points` in the summary JSON (grouped
  by location, `export.service_locations`): where visible services share a spot the dot is split
  into equal slices (canvas icons made on demand, `styleimagemissing`); one visible service = a full
  dot. Libraries pinned on unpkg: `maplibre-gl@4.7.1`, `pmtiles@3.2.1`. `?city=<slug>`
  selects the data files. No build step: `docs/index.html`, `app.js`, `style.css`.
- Attribution for Overture (ODbL/CDLA per theme), OSM-derived data, Census, basemap.
- Must work locally with `cd docs && python -m RangeHTTPServer 8000` (http://localhost:8000) and on
  GitHub Pages. PMTiles needs HTTP Range requests: **never** use `python -m http.server` for preview — it
  ignores Range headers and the map loads with no data and no obvious error.

### Phase 4 — Portfolio

- `README.md` already exists with a **Development environment** section (why WSL, why Docker, the
  extra steps, what could have been done differently). Keep it and keep it current: when a new
  environment issue or workaround comes up, add it to that section (WSL-caused vs not). Fill the
  TODO placeholders as each phase finishes; don't delete that section.
- `README.md` keeps its current case-study section order (see also `CLAUDE.local.md` if present); matter-of-fact
  voice, specific numbers, CRS stated, data licenses per dataset, "What I learned" written last
  and by me (Claude drafts only the factual sections unless asked).
- Screenshot `images/headline.png` (Playwright headless capture of the deployed page is fine).
- Reproduce in ≤3 commands: `conda env create …`, `make all CITY=carson_city`, `make serve` (`make serve` = `cd docs && python -m RangeHTTPServer 8000`).

## 7. Working agreements for Claude Code

- **Ask before**: dropping a schema or database, changing `docker` state of the shared container,
  rewriting committed notebooks wholesale, adding a new dependency to `environment.yml`.
- The kartoza container is shared with other projects — only touch the `urban_access` database.
- Notebooks: numbered, run top-to-bottom, first cell loads config for `CITY` and sets paths;
  no hard-coded bbox, paths, or credentials. Move any function used twice into `src/`.
  **Execute notebooks in place**, in numeric order, so the outputs are saved in `notebooks/*.ipynb`:
  `jupyter nbconvert --to notebook --execute --inplace --ExecutePreprocessor.kernel_name=urban-access notebooks/<nn>_<name>.ipynb`.
  Never execute into a scratchpad or other copy. Outputs stay in the local files; the `nbstripout`
  git filter removes them only from what is committed.
- SQL lives in `sql/*.sql`, parameterised by schema; no SQL strings longer than a few lines in Python.
- Units: metres in the analysis CRS everywhere. Name columns with units (`_m`, `_pct`).
- Style: `ruff` for lint/format; type hints in `src/`; `pytest` for scoring math and config parsing.
- Large files never go in git (raw data, intermediate GeoJSON). `docs/data/*.pmtiles` is the exception.
- Commit messages: imperative, one topic per commit.
- When a number in the README or a popup changes, regenerate it from the pipeline — never edit by hand.

## 8. Open items for Plan Mode (TBD-PLAN)

Resolved in Phase 1 planning (2026-10-30):

1. ~~Service mapping~~ → `config/scoring.yaml` `services:` (taxonomy hierarchy match) + per-city
   `services:` name include/exclude lists in `config/cities/<slug>.yaml`. Set from
   `data/interim/<slug>/service_review.csv` (notebook 00). Field-checked single places are dropped or moved
   by Overture id in the city's `place_overrides:` (status `removed` in the review table). Match places
   across files by `ref` (first 8 characters of the id), never by `fid`, which is load order. Grocery = full-service only (convenience
   stores are a side metric); healthcare = pharmacy + primary/urgent care + hospital (no
   `doctors_office`, dental, eye, EMS).
4. ~~JAC GTFS URL~~ → `https://JumpAroundCarson.transloc.com/Secure/Admin/Reports/GTFSDownload.aspx`
   (the carson.org document link is dead). Zip cached in `data/raw/<slug>/gtfs.zip`.
5. ~~School levels~~ → **K-8 only**: open, not full-virtual, NCES grade span overlaps K-8 (combined
   schools such as 1-12 count). Non-elementary/middle tags via the city include list; checked with `make xcheck`.
7. ~~Makefile / driver~~ → **CLI-driven**: `python -m urban_access.cli <step> --city <slug>`; the
   Makefile calls the CLI; notebooks call the same `src/` functions and add checks and plots. No papermill.

Resolved in Phase 2 planning (2026-10-30):

2. ~~Walkable filter / components~~ → drop motorways and foot-denied segments; keep trunk and
   `as_private`; largest component only (§6 Phase 2 step 2).
3. ~~Residential heuristic~~ → residential classes incl. mobile homes + unclassified 40–1,500 m²
   without a non-residential place; weight area × floors (§6 Phase 2 step 5).

Resolved in Phase 3 planning (2026-10-30):

6. ~~Basemap; colour ramp and classes~~ → OpenFreeMap `positron` (no key); 7 fixed absolute classes
   0–10 … 50–60, 60–100, viridis; all in `config/scoring.yaml` `web:` (passed to the page via the
   summary JSON).

Still open:

8. Optional: H3 summary layer or a second city to prove the config is portable.
