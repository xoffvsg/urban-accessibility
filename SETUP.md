# Environment setup — VS Code + WSL2 Ubuntu (bash)

Run every command in the VS Code integrated terminal connected to **WSL: Ubuntu**
(green "WSL: Ubuntu" badge bottom-left). Do the same on the desktop and the laptop.

## 1. VS Code

Windows side: install the **WSL** extension. Then from an Ubuntu terminal:

The project lives on the Windows drive at `C:\dev\PortfolioProject4\Urban_Accessibility`,
which WSL sees as `/mnt/c/dev/PortfolioProject4/Urban_Accessibility`.

```bash
mkdir -p /mnt/c/dev/PortfolioProject4 && cd /mnt/c/dev/PortfolioProject4
git clone https://github.com/xoffvsg/urban-accessibility.git Urban_Accessibility
cd Urban_Accessibility
git config core.fileMode false      # /mnt/c reports every file as executable
git config core.autocrlf input      # with .gitattributes: LF in the repo
code .                              # reopens VS Code attached to WSL
```

If the GitHub repo is still empty, unzip the starter kit into that folder instead and run
`git init -b main && git remote add origin https://github.com/xoffvsg/urban-accessibility.git`
before the two `git config` lines.

Use git from the WSL terminal only for this repo (not GitHub Desktop / Windows git), so line
endings and file modes stay consistent on both machines. Opening the folder from Windows
(QGIS, Explorer) is fine.

Install these extensions *in WSL* (VS Code prompts "Install in WSL"): Python, Jupyter, Ruff,
YAML, SQLTools + SQLTools PostgreSQL driver (optional), Claude Code.

## 2. Miniforge in WSL (skip if `conda` already works in Ubuntu)

```bash
curl -L -O https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh
bash Miniforge3-Linux-x86_64.sh -b -p ~/miniforge3
~/miniforge3/bin/conda init bash
exec bash
```

## 3. Project environment

```bash
conda env create -f environment.yml        # installs all packages (later: conda env update -f environment.yml --prune)
conda activate urban-access
# one-time setup steps that USE packages installed above:
python -m ipykernel install --user --name urban-access --display-name "Python (urban-access)"   # register Jupyter kernel
nbstripout --install                       # add git filter to this repo (run after git init)
playwright install --with-deps chromium    # download browser for README screenshot (optional until Phase 4)
```

Check the toolchain resolves inside the env:

```bash
which python ogr2ogr tippecanoe            # all under ~/miniforge3/envs/urban-access/
ogr2ogr --formats | grep -E "Parquet|PostgreSQL"
tippecanoe --version                       # >= 2.17
python -c "import duckdb, geopandas; print(duckdb.__version__, geopandas.__version__)"
```

In VS Code: `Ctrl+Shift+P → Python: Select Interpreter → urban-access`, and pick the
"Python (urban-access)" kernel in notebooks.

## 4. Secrets

```bash
cp .env.example .env
# edit .env: PGUSER, PGPASSWORD (your kartoza credentials), CENSUS_API_KEY
```

## 5. Database — existing kartoza/postgis container (port 5436)

Docker Desktop → Settings → Resources → WSL integration → enable for Ubuntu, so `docker` and
`psql`-over-localhost work from WSL. Then:

```bash
set -a; source .env; set +a
docker ps --format '{{.Names}}\t{{.Image}}\t{{.Ports}}'      # find the kartoza container (…:5436->5432)

# psql client (once)
sudo apt-get update && sudo apt-get install -y postgresql-client

# create the project database + extensions (idempotent)
psql "host=$PGHOST port=$PGPORT user=$PGUSER dbname=postgres" -c "CREATE DATABASE urban_access;" || true
psql -c "CREATE EXTENSION IF NOT EXISTS postgis;"
psql -c "CREATE EXTENSION IF NOT EXISTS pgrouting;"
psql -c "SELECT postgis_full_version(); SELECT pgr_version();"
```

If `pgrouting` is not available in your kartoza image (`SELECT name FROM pg_available_extensions WHERE name='pgrouting';`
returns nothing), stop and decide in Plan Mode — the kartoza images normally ship it.

## 6. Smoke test

```bash
python - <<'EOF'
import duckdb
con = duckdb.connect()
con.sql("INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2';")
print(con.sql("""
  SELECT count(*) FROM read_parquet(
    's3://overturemaps-us-west-2/release/2026-10-21.0/theme=places/type=place/*', hive_partitioning=1)
  WHERE bbox.xmin > -119.8 AND bbox.xmax < -119.7 AND bbox.ymin > 39.0 AND bbox.ymax < 39.2
""").fetchall())
EOF
```

A non-zero count means S3 access, DuckDB and the release string are all good.

## 7. Start Claude Code

```bash
claude            # in the repo root; it reads CLAUDE.md
```

Switch to Plan Mode (Shift+Tab) and start from section 8 "Open items" in CLAUDE.md.
