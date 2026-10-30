# Walking Access to Essential Services — Carson City, NV

🗺️ **[Live demo](https://xoffvsg.github.io/urban-accessibility/)** *(not yet deployed)*

<!-- TODO (Phase 4): one-paragraph intro — what this is, what it does, why it matters. 3–4 sentences, specific. -->

<!-- TODO (Phase 4): ![Screenshot showing the headline finding](images/headline.png) -->

---

## The question

Which neighborhoods in Carson City have the best — and worst — walking access to essential services
(grocery, healthcare, parks, schools, transit)?

---

## The data

<!-- TODO (Phase 2–4): one line per dataset: name, feature count, source link, license.
     Overture places / buildings / transportation / base land_use / divisions (release 2026-10-21.0),
     TIGER/Line block groups, ACS 5-year, JAC GTFS (if used). End with the CRS (EPSG:32611). -->

---

## Methodology

<!-- TODO (Phase 2–4): extract → transform → visualize, plus the non-obvious choices:
     block groups instead of tracts, network distance instead of buffers, tiered 5/10/20-minute bands,
     dasymetric population on residential buildings. -->

---

## Findings

<!-- TODO (Phase 2b–4): headline finding, 3–5 numbers, top/bottom table. -->

---

## Tech stack

<!-- TODO (Phase 4): full stack list with a one-line reason per tool. The environment and
     database choices are already documented in the next section. -->

---

## Development environment

This project was built on Windows 11 using **VS Code attached to WSL2 Ubuntu**, with a bash terminal,
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
walking distances) and by the course goal of practising a PostGIS workflow. A pure-Python alternative
would be OSMnx/NetworkX on an OpenStreetMap walk network.

### Extra steps WSL added (one-time)

| Step | Why it was needed |
|---|---|
| Install the VS Code **WSL** extension, then *Open a WSL Window* / `code .` from Ubuntu | VS Code runs a server inside Ubuntu |
| Reinstall Python, Jupyter, Claude Code extensions **on the WSL side** | Extensions installed for Windows don't apply to WSL windows |
| Install **Miniforge in Ubuntu**, create the env, select the interpreter by path | Windows conda environments aren't visible from Linux |
| `git config core.fileMode false`, `core.autocrlf input`, `.gitattributes` with LF | Files on `/mnt/c` look executable to Linux; Windows and Linux use different line endings |
| Set **git identity** and a **credential helper** in WSL | WSL git has its own config, separate from Windows git |
| Know the **Ubuntu user password** for `sudo` | Needed for `apt-get` (e.g. the `psql` client) |
| Fix `docker ps` → *permission denied* on `docker.sock` | Docker Desktop's WSL integration plus membership of the `docker` group. Not needed for the project itself, which connects through port 5436 |
| Dismiss the *"workspace is on the Windows file system"* warning | The repo is on `C:` on purpose; file access from WSL is slower there |

Issues that came up during setup but **were not caused by WSL** (they'd happen on Windows too):
`psql` waiting at `(END)` (pager; use `-P pager=off`), `load_dotenv()` failing from a heredoc script
(pass an explicit `.env` path), DuckDB 2.0 removing `.fetchone()` on relations, and pgRouting 4 removing
`pgr_createTopology` and related functions.

### What could have been done differently

- **Windows-native, as in my earlier projects:** a Windows conda environment, VS Code with Git Bash,
  and the same Docker container. That avoids every step in the table above; tippecanoe can run as it
  did in nyc-hydrant-map.
- **Hybrid:** stay on Windows and use WSL only for a single command (e.g. `wsl tippecanoe …`).
- **All-Linux:** keep the repo in the Linux filesystem (`~/dev/...`) for faster I/O, at the cost of
  Windows tools reaching it through `\\wsl$\Ubuntu\...`.
- **Define the database in the repo:** add a `docker-compose.yml` that pins the image and enables
  pgRouting, so a reader can reproduce the database with one command instead of relying on an
  existing container.

<!-- TODO (end of project): add a verdict on the experiment — did WSL pay off in daily use? -->

---

## How to reproduce

<!-- TODO (Phase 4): three commands or fewer. Full environment steps are in SETUP.md. -->

Requires: Windows with WSL2 Ubuntu (or any Linux/macOS), conda (Miniforge), and a PostgreSQL server with
PostGIS and pgRouting (Docker `kartoza/postgis` used here). See [SETUP.md](SETUP.md).

---

## What I learned

<!-- TODO (end of project): written by me, in first person. -->

---

## Author

<!-- TODO -->

---

## License

<!-- TODO: MIT for code; data licenses listed under "The data". -->
