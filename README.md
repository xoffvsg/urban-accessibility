# Walking Access to Essential Services — Carson City, NV

🗺️ **[Live demo](https://xoffvsg.github.io/urban-accessibility/)**

An interactive map that scores each of Carson City's 46 neighborhoods (Census block groups) from 0
to 100 on how far its 62,050 residents walk, along the real street and path network, to a grocery
store, healthcare, a transit stop, a K-8 school and a park. Only 2.2 % of residents live within a
5-minute walk of a full-service grocery store, and walking access is better in the lower-income
neighborhoods than in the higher-income ones (mean score 48.7 for the lowest-income third of
residents, 24.6 for the highest). Every service list was
checked against public registries and field knowledge, and the whole map runs from open data as
static files: no server, $0 per month.

![Carson City walking-access map: neighborhoods coloured by score, service points, and the share of residents within a 5-minute walk of each service](images/headline.png)


---

## The objective

This project is the continuation of the work started with ([nyc-hydrant-analysis](https://github.com/xoffvsg/nyc-hydrant-analysis) and [nyc-hydrant-map](https://github.com/xoffvsg/nyc-hydrant-map) to become more familiar with modern GIS techniques.

This Urban Accessibility question is a pretext to try for the first time:
- DuckDB
- Overture data obtained from S3 servers
- pgRouting to calculate actual distances along a path and not only relying on a buffer zone
- WSL2 Ubuntu
- Claude Code as a coding agent in VS Code

I chose Carson City, NV as a test case to validate the results as I am familiar with the city, which would not have been the case had I chosen a larger town unknown to me. The code uses a bounding box to define the area to investigate so it be used to evaluate other cities (untested as of now)

---


## What I learned

- The quality of the Overture data was questionable and services were tagged or located incorrectly. It required to develop cross-checks against SNAP, NPPES, HRSA and NCES to confirm some places but each had its own errors (a wrong SNAP address, NPI records for closed clinics, etc.). It complicated the initial scope of this project.
- The OSM map was either missing foot trails or had gates incorrectly flagged open. Some walking paths were initially calculated by routing them through gated communities not identified as such. 172 gates unflagged would have to be surveyed and characterized. A small number of gates and gated communities were updated/added to validate the experiment.
- pgRouting 4.0.1 multi-source equicost returned non-minimal distances (506 m vs 453 m); a virtual super-source fixed it, verified pair by pair against Dijkstra.
- Calculating the distances to parks by identifying them by a single point rather than by all the vertices of their polygon cut down the calculation time dramatically (485 s -> 38 s)
- Building footprints cannot be systematically be considered as dwellings: Douglas County had 25-45 % more "homes" than households (sheds, barns).
- All of the above makes risky an overreliance of free open source data such as Overture or OSM for mission-critical evaluations. I would not be able to validate the results made for a different city.
- Configuring VS Code to run WSL2 Ubuntu was more involved than expected. Claude helped a lot.
- Using Claude Code in VS Code is easy and efficient.



---

## The question

Which neighborhoods in Carson City have the best — and worst — walking access to essential services
(grocery, healthcare, parks, schools, transit)?

---

## The data

Counts are for the fetch extent: the city bbox plus the block groups scored for it (centroid inside
the bbox), buffered 2 km so services just outside still count.

| Dataset | Features | Source | License |
|---|---|---|---|
| Overture places (`places/place`) | 4,858 | [Overture Maps](https://overturemaps.org), release `2026-10-21.0` | CDLA-Permissive-2.0 |
| Overture buildings | 38,554 | Overture `buildings/building` | ODbL (OSM-derived) |
| Overture road segments / connectors | 16,650 / 24,430 | Overture `transportation` | ODbL |
| Overture park land use | 98 polygons (+ 11 city polygons, see below) | Overture `base/land_use` (park, dog park, playground, recreation ground) | ODbL |
| Census block groups | 80 (46 scored: 40 Carson City, 6 Douglas County) | TIGER/Line 2024 via [pygris](https://github.com/walkerke/pygris) | Public domain |
| ACS 5-year 2020–2024 | 80 block groups | [Census API](https://www.census.gov/data/developers/data-sets/acs-5year.html): population, age (B01001), income (B19013), vehicles (B25044), income-to-poverty ratio (C17002), with MOEs | Public domain |
| OSM gates and fences | 300 gates / 302 fence lines | [Overpass API](https://overpass-api.de), fetched 2026-10-30 (`barrier=*`) | ODbL, © OpenStreetMap contributors |
| NCES school lists (cross-check) | 19 schools (13 public, 6 private) | [NCES EDGE](https://nces.ed.gov/programs/edge/) public school locations + administrative data 2024-25 (grade span, status); [Private School Universe Survey](https://nces.ed.gov/surveys/pss/) 2023-24, 2021-22 for non-respondents | Public domain |
| USDA SNAP authorized retailers (cross-check) | 54 retailers | [SNAP retailer locations](https://www.fns.usda.gov/snap/retailer-locator) (live ArcGIS layer), fetched 2026-10-31 | Public domain |
| CMS NPPES organizations (cross-check) | 732 NPIs in 5 ZIP codes, 416 geocoded inside the extent | [NPI Registry API](https://npiregistry.cms.hhs.gov/api-page), geocoded with the [Census Geocoder](https://geocoding.geo.census.gov/), fetched 2026-10-31 | Public (CMS); geocoder public domain |
| HRSA health center sites (cross-check) | 3 sites | [data.hrsa.gov](https://data.hrsa.gov/data/download) service delivery sites | Public domain |
| Carson City parks | 110 polygons (11 added to the parks layer) | Carson City GIS "Carson City Parks and Open Space" (2023) | No licence stated; used with attribution |
| Carson City land use (homes) | 21,271 parcels, 24,734 dwelling units | Carson City GIS "Existing Land Use" (assessor, 2024): dwelling units and land-use category only | No licence stated; used with attribution; owner fields not used |
| Douglas County zoning and parcels (homes) | 8,358 parcels | Douglas County GIS zoning + parcel outlines: zoning code and geometry only | No licence stated; used with attribution; owner fields not used |
| JAC transit stops (GTFS) | 184 served stops | [Jump Around Carson](https://www.carsoncity.gov/government/departments-g-z/public-works/transportation/jac-jump-around-carson) feed via TransLoc | Published by Carson City |

Services come from Overture places, matched on `taxonomy.hierarchy` (see `config/scoring.yaml`). City name
overrides fix mis-tagged grocers (e.g. Raley's is tagged `coffee_shop`, Walmart `department_store`).
Places checked on the ground are dropped or moved one by one (`place_overrides`, keyed by Overture id):
for groceries, one Walmart is now a Ross store, three listed stores don't exist, and one point was moved
to its store. A cross-check with USDA SNAP authorized retailers (`make xcheck`) confirmed 11 of those
12 stores and found two full-service markets Overture tags as a restaurant and a butcher (Hacienda
Market and Grill, Centro Market); Kabayan Asian Multi Mart, absent from SNAP, was dropped as not
full-service, which leaves 13 grocery stores. SNAP store types alone weren't enough: its "Grocery
Store" type also covers a pizza chain and a meat shop, and one SNAP address was wrong.
Healthcare was checked against the CMS NPI registry (organizations, geocoded) and HRSA health center
sites: a place counts when an organization with a matching name at that site has a primary-care,
urgent-care, general-hospital or pharmacy taxonomy. That removed 4 specialists Overture tags as
clinics or hospitals (endoscopy, cardiology, oncology, medical equipment), added 8 places Overture
tags `doctors_office` / `health_care` / outpatient (an urgent care, family and internal medicine
practices, and the main hospital), and counts the in-store pharmacies of Raley's, Smith's and Costco.
A field check then removed four Carson Tahoe clinic records that have closed (the two Walmart
clinics and two physician-clinic sites, one of them added from the registry, which still lists it),
which leaves 29 places. Sites the registry geocodes far from the Overture point (big stores) are kept
but not confirmed; pharmacies inside a big store use the store's own point.
Schools were checked against NCES (`make xcheck`): a school counts if it is open, not online-only,
and teaches any of grades K-8. 13 of the 14 schools first selected matched an NCES school with a K-8
grade span, within about 100 m. The 14th, Capital Christian School, has no NCES record; Overture lists
the same site again as Legacy Christian Academy, which NCES has as K-8, so the old name was dropped.
Overture also missed two schools that teach K-8 grades: Grace Christian Academy (tagged
`private_school`) and iSchool (grades 1-12, tagged `college_counseling`). That leaves 16 schools.
Overture has no grade levels, so without NCES the private schools' grades could not be checked.
Parks combine Overture land use with 11 Carson City GIS park polygons that Overture lacks (e.g.
Mayor's Park, Imus Park, Lone Mountain Park); the city layer covers Carson City only, so Douglas
County parks come from Overture alone.
Transit uses GTFS, because the Overture transit places here are Greyhound stops and heritage-rail depots.

ACS caveat: at block-group level the coefficient of variation is above 30 % in 34 of the 45 populated
scored block groups for the share of households without a car, in 27 for the share below 200 % of the
poverty line, and in 11 for median income. These fields are shown with their margins of error, and the
equity results carry Monte Carlo ranges (see Findings).

Analysis CRS: **EPSG:32611** (WGS 84 / UTM 11N, metres). Web layers are exported in EPSG:4326.

---

## Methodology

**Extract.** DuckDB reads Overture GeoParquet straight from S3, filtering on the `bbox` columns, for the
bbox plus the scored block groups, buffered 2 km. TIGER block groups and ACS come from pygris and the
Census API. JAC stops come from the GTFS feed.

**Walk network.** All Overture road and path segments except motorways and segments where foot access is
denied, split at Overture connectors into 36,346 undirected edges (cost = length in metres, EPSG:32611)
and loaded into PostGIS. Routing uses the largest connected component, which holds 98.8 % of the network
length. pgRouting 4.0.1 computes the distances.

**Origins: where people live.** Each block group's ACS population is spread over its homes
(dasymetric allocation), placed from parcel dwelling units: Carson City's land-use layer gives the
dwelling units of each parcel (24,734 in total, close to the Census count of households), and in
Douglas County each parcel in a residential zone counts as one home (multi-family zones by floor
area). A parcel's units go to its main building, or are shared between its buildings when there are
several; sheds, garages and buildings on commercial or vacant parcels get none. That gives 19,836
home buildings plus 1,578 parcels with dwelling units but no Overture footprint of their own, placed
at the parcel (mostly small single-unit lots of about 365 m²: likely townhouses whose shared building
is counted on a neighbouring lot, or homes newer than the footprints). Across block groups, parcel units per ACS household fall between 0.74 and
1.75, mostly 0.9–1.3. Access is measured from every home and averaged per block group, weighted by
population, so a block group's empty hills don't count as people without access. (Before the parcel
data, homes were guessed from building footprints: in Douglas County that counted 25–45 % more homes
than households, because sheds and barns were included.)

**Distances.** Homes and services are snapped to the nearest walkable edge, not the nearest
intersection, because blocks here are often 100–200 m long. The distance to the nearest service of each
type comes from a single-source driving-distance run out of a virtual node connected to every service.
Multi-source `equicost` in pgRouting 4.0.1 returned non-minimal distances, and the switch was verified
against point-to-point Dijkstra (0.00 m difference on 40 random pairs). Parks are reached at any point
of their boundary within 30 m of a path. Transit means GTFS stops.

**Scoring.** Tiered walk bands: ≤ 400 m (about 5 min) = 1.0, ≤ 800 m = 0.6, ≤ 1,600 m = 0.3, beyond = 0.
Per service, the score is the population-weighted mean × 100. The composite `access_score` is the
weighted sum (grocery 0.25, healthcare 0.20, transit 0.20, schools 0.20, parks 0.15). Scores are absolute,
not rescaled, so other cities are comparable; a city-relative percentile is stored alongside.

**Why block groups.** There are 46 here against roughly a dozen tracts, which is enough resolution to see
neighbourhood differences. The cost is large ACS margins of error (see The data).

**Limitations.** Roads without a mapped sidewalk are treated as walkable. Access rules that apply to
part of a segment are applied to the whole segment. Overture has no barriers (fences, gates). OSM gates
explicitly closed to walkers (`access=no` / `foot=no`, 7 here) are applied automatically; the other
OSM gates (most have no access tag) are not modelled. Gates checked on the ground are drawn as
lines in `carson_city_gates.geojson` (open or closed), and blocked paths are listed by segment id
(`blocked_segments`). Gated
communities are drawn as polygons whose only connections are their real gates (`gated_areas`); so far two
adjoining ones off Fairview Dr and S Saliman Rd (136 and 108 homes), each with a single exit. Overture place categories need city-specific
corrections (see `config/cities/carson_city.yaml`). Healthcare still includes some specialist clinics
that Overture tags as general clinics.

---

## Findings

**Few residents can walk to a grocery store in 5 minutes, and access runs opposite to income: the
central neighborhoods score highest, the south of Carson City and the Douglas County neighborhoods
lowest.**

<!-- Numbers come from notebooks/05–06 (`make equity`); the table from `python scripts/readme_table.py`. -->

| Rank | Neighborhood | Census block group | Access score | Residents | Grocery ≤ 5 min |
|---:|---|---|---:|---:|---:|
| 1 | Capitol Complex area | 1-1 | 62.9 | 1,002 | 7 % |
| 2 | Airport Rd / Lincoln Hwy | 10.01-2 | 62.3 | 2,024 | 13 % |
| 3 | Treadway Park area | 2-3 | 61.2 | 916 | 0 % |
| 4 | Fulstone Family Park area | 5.02-3 | 61.0 | 1,755 | 28 % |
| 5 | E William St / E Long St | 5.02-2 | 58.2 | 749 | 6 % |
| … | | | | | |
| 41 | South Sunridge Park area | 21-1 | 9.8 | 1,954 | 0 % |
| 42 | Johnson Ln / Vicky Ln | 22-4 | 8.2 | 1,265 | 0 % |
| 43 | Stephanie Way | 22-5 | 7.6 | 1,334 | 0 % |
| 44 | Silver Saddle Ranch area | 8-3 | 6.4 | 3,054 | 0 % |
| 45 | Johnson Lane Park area | 22-1 | 6.1 | 1,214 | 0 % |

Scores use the default weights (grocery 25 %, healthcare 20 %, transit 20 %, schools 20 %, parks
15 %); "Grocery ≤ 5 min" is the share of the neighborhood's residents within 400 m of a full-service
grocery store.

All numbers are for the 62,051 residents of the 45 populated scored block groups. Ranges in brackets
are the 5th–95th percentiles of 1,000 Monte Carlo draws from the ACS margins of error.

**A 5-minute walk to a grocery store is rare.** 1,390 residents (2.2 % [2.0–2.5]) live within 400 m
of one of the 13 full-service grocery stores, measured along the walking network; 44.1 % have none
within 1,600 m (about 20 minutes). Another 4,590 (7.4 %) have a convenience store but no grocery
within 400 m. Transit is the most walkable service (33.8 % within 400 m of a JAC stop), then parks
(25.4 %); healthcare (3.5 %) and K-8 schools (3.6 %) are almost as rare as groceries.

**Access is better where incomes are lower.** The lowest-income third of residents (block-group median
household income $34k–61k) has a mean access score of 48.7; the highest-income third ($82k–143k) has
24.6. Across block groups, Spearman ρ between the access score and median household income is −0.59
(MC range −0.60 to −0.31), and +0.48 with the share of residents below 200 % of the poverty line.
Residents below that line are more likely than average to be within 400 m of a transit stop (47.5 %
[43.7–51.2] vs 33.8 %) and of a grocery store (4.0 % vs 2.2 %); so are households without a car
(46.5 % and 4.3 %).

**Older residents have less access.** 1.9 % [1.6–2.2] of residents 65+ live within 400 m of a
grocery store, and their mean access score is 32.4 against 35.5 for all residents (ρ with the 65+
share: −0.33).

**Low access clusters in the south.** The access score is strongly clustered (global Moran's I = 0.66,
p = 0.0001, 9,999 permutations). Local Moran's I finds 9 low-low block groups at p < 0.05 (6 after a
false-discovery-rate correction), all between 39.03 °N and 39.15 °N: the Douglas County block groups
and three in the south of Carson City. The 8 high-high block groups (4 after correction) lie further
north, at 39.17–39.18 °N.

These are block-group comparisons (ecological inference): a group is assumed to live in its block
group's homes like everyone else.

---

## Tech stack

- **Extract: DuckDB** (`spatial` + `httpfs`). It reads Overture's GeoParquet straight from S3,
  filtering on the bounding-box columns, so only Carson City's rows are downloaded, not a planet file.
- **Analysis: PostGIS + pgRouting** in Docker. Routing runs in SQL next to the data: 36,000 edges,
  21,000 homes and 4,600 service access points, one search per service or per destination group.
  The full network rebuild with all distances takes about 2 minutes.
- **Scoring and statistics: Python** (geopandas, pandas, esda), with pytest for the scoring rules,
  the cross-checks and the config parser. Notebooks show each step; reusable code lives in
  `src/urban_access/` and runs from one CLI (`make <step>`).
- **Tiles: tippecanoe → PMTiles.** One static file (375 KB) that the browser reads with HTTP range
  requests, so no tile server is needed.
- **Map: MapLibre GL JS** with the `pmtiles` protocol. It's open source, renders vector data, and
  lets the page re-weight the five services in the browser without any backend.
- **Hosting: GitHub Pages.** The `docs/` folder is the site.

**Cost: $0 per month. Servers running: none at view time** (the database is only used to build the
files).

The aim was a map anyone can open and anyone can rebuild. That ruled out a tile server or an API
behind the map, and paid basemaps or keyed services (Mapbox, Google): the basemap is OpenFreeMap. No
proprietary map (Google, Apple) was used as a data source either, because their terms forbid deriving
data. Every input is open data or a public registry, cited in The data. The environment and database
choices are explained in the next section.

---

## Development environment

This project was built on a Windows 10 desktop using **VS Code attached to WSL2 Ubuntu**, with a bash terminal,
a conda environment inside Ubuntu, and PostGIS + pgRouting in a Docker container. That is not the
simplest setup that would have worked, and the reasons are recorded here.


### Why WSL2 Ubuntu

- **One Linux toolchain.** GDAL/ogr2ogr, tippecanoe, `psql`, DuckDB and Python all come from a
  single conda-forge environment (`environment.yml`) in Linux. It avoids mixing Windows and Linux
  builds, and it sidesteps an older Windows Anaconda GDAL (3.6) whose `ogr2ogr` crashes on exit.
- **The brief asked for a bash terminal**, and bash scripts and Makefiles run unchanged in Linux.
- **Deliberate experiment.** I had already done comparable projects without WSL
  ([nyc-hydrant-analysis](https://github.com/xoffvsg/nyc-hydrant-analysis),
  [nyc-hydrant-map](https://github.com/xoffvsg/nyc-hydrant-map) — including tippecanoe), so WSL
  was **not required**. I used it here to compare the two ways of working.

The project folder stays on the Windows drive (`C:\dev\PortfolioProject4\Urban_Accessibility`,
`/mnt/c/...` in WSL), so QGIS and pgAdmin on Windows can open the same files.

### Why a Docker container for the database

- **pgRouting comes with the image.** The `kartoza/postgis` image ships PostGIS and pgRouting
  (here PostgreSQL 18.6, pgRouting 4.0.1). Native Windows PostgreSQL needs pgRouting added separately.
- **Isolation.** The machine also has native PostgreSQL 14, 16 and 18 installs. The container has its
  own port (5436), its own data volume and its own versions, so nothing collides.
- **Shared, but separated.** The same container serves other projects. This project only uses its own
  database (`urban_access`) and one schema per city.
- **Same behaviour on every machine.** The desktop and the laptop run the same image, so SQL written
  on one runs on the other.

Is a server database necessary at all? Not for the data size: Carson City fits easily in memory, and
DuckDB alone could do most of the spatial joins. The database is justified by **pgRouting** (network
walking distances) and by the course goal of practising a PostGIS workflow.


### What could have been done differently

- **Windows-native, as in my earlier projects:** a Windows conda environment, VS Code with Git Bash,
  and the same Docker container. Tippecanoe can run as it did in nyc-hydrant-map.
- **Hybrid:** stay on Windows and use WSL only for a single command (e.g. `wsl tippecanoe …`).
- **All-Linux:** keep the repo in the Linux filesystem (`~/dev/...`) for faster I/O, at the cost of
  Windows tools reaching it through `\\wsl$\Ubuntu\...`.
- **Define the database in the repo:** add a `docker-compose.yml` that pins the image and enables
  pgRouting, so a reader can reproduce the database with one command instead of relying on an
  existing container.



---

## How to reproduce

Requires: Windows with WSL2 Ubuntu (or any Linux/macOS), conda (Miniforge), a PostgreSQL server with
PostGIS and pgRouting (Docker `kartoza/postgis` used here), and a free Census API key in `.env`. See
[SETUP.md](SETUP.md).

```bash
git clone https://github.com/xoffvsg/urban-accessibility.git && cd urban-accessibility
conda env create -f environment.yml && conda activate urban-access   # once; then fill .env
make all CITY=carson_city      # extract → network + scores → equity → docs/data/*.pmtiles
make serve                     # http://localhost:8000
```

RangeHTTPServer is used instead of `python -m http.server` because PMTiles reads byte ranges of the
tile file, which the built-in server does not support.

`make all` fetches some sources live (USDA SNAP retailers, OpenStreetMap barriers, the counties'
parcel layers), so a rebuild can differ slightly from the published map. Overture releases also expire
from S3 after a few months; the cached raw files in `data/raw/` are the record. `make xcheck
SOURCE=all` regenerates the cross-check review tables in `data/interim/<city>/`.

---


## Possible next steps

- Check walk edges that cross OpenStreetMap fences away from any gate, with someone who knows the
  ground; fences and gated entries are the network's biggest unknown.
- Run a second city from a new config file, to prove the pipeline is portable.
- Treat group quarters (ACS B26001) separately: one neighborhood's residents are probably mostly in a
  correctional or care facility, yet they're spread over homes like households.
- Field-check the healthcare places the provider registry couldn't confirm.
- Weight transit stops by service frequency, not just distance.

---



## License

Code: MIT, see [LICENSE](LICENSE). Data: each dataset's licence is listed under
[The data](#the-data).
