"""PostGIS access: SQLAlchemy engine, psql runner for sql/*.sql, ogr2ogr loader."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import geopandas as gpd
import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.engine import URL, Engine

from .config import REPO_ROOT, CityConfig, settings
from .services import ASSIGNED, classify_places

SQL_DIR = REPO_ROOT / "sql"

# raw GeoParquet → table name; polygons are promoted to multi
TABLES: dict[str, tuple[str, str | None]] = {
    "places": ("places.parquet", "POINT"),
    "buildings": ("buildings.parquet", "PROMOTE_TO_MULTI"),
    "segments": ("segments.parquet", "LINESTRING"),
    "connectors": ("connectors.parquet", "POINT"),
    "parks": ("parks.parquet", "PROMOTE_TO_MULTI"),
    "neighborhoods": ("neighborhoods.parquet", "PROMOTE_TO_MULTI"),
}


def engine() -> Engine:
    s = settings()
    url = URL.create(
        "postgresql+psycopg",
        username=s.pguser,
        password=s.pgpassword,
        host=s.pghost,
        port=s.pgport,
        database=s.pgdatabase,
    )
    return create_engine(url)


def _pg_env() -> dict[str, str]:
    """Environment for psql / ogr2ogr: credentials via PG* variables, never on the command line."""
    s = settings()
    env = dict(os.environ)
    env.update(PGHOST=s.pghost, PGPORT=str(s.pgport), PGDATABASE=s.pgdatabase)
    if s.pguser:
        env["PGUSER"] = s.pguser
    if s.pgpassword:
        env["PGPASSWORD"] = s.pgpassword
    return env


def run_sql(name: str, cfg: CityConfig, **variables: object) -> float:
    """Run sql/<name> with psql; `schema` and `epsg` are always passed. Returns seconds."""
    path = SQL_DIR / name
    vars_ = {"schema": cfg.slug, "epsg": cfg.analysis_epsg, **variables}
    cmd = ["psql", "-X", "-q", "-P", "pager=off", "-v", "ON_ERROR_STOP=1"]
    for k, v in vars_.items():
        cmd += ["-v", f"{k}={v}"]
    cmd += ["-f", str(path)]
    t0 = time.time()
    res = subprocess.run(cmd, env=_pg_env(), capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"{name} failed:\n{res.stderr.strip()}")
    for line in res.stdout.splitlines():
        if line.strip():
            print(f"  {line}")
    return time.time() - t0


def load_parquet(path: Path, table: str, cfg: CityConfig, nlt: str | None = None) -> None:
    """Load a (Geo)Parquet file into <slug>.<table> with ogr2ogr (reprojected to analysis CRS)."""
    s = settings()
    cmd = [
        "ogr2ogr",
        "-f",
        "PostgreSQL",
        f"PG:host={s.pghost} port={s.pgport} dbname={s.pgdatabase}",
        str(path),
        "-nln",
        f"{cfg.slug}.{table}",
        "-overwrite",
        "-lco",
        "GEOMETRY_NAME=geom",
        "-lco",
        "FID=fid",
        "-lco",
        "SPATIAL_INDEX=GIST",
        "-lco",
        "LAUNDER=YES",
        "--config",
        "PG_USE_COPY",
        "YES",
    ]
    if nlt is not None:
        cmd += ["-t_srs", f"EPSG:{cfg.analysis_epsg}", "-nlt", nlt]
    res = subprocess.run(cmd, env=_pg_env(), capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"ogr2ogr {path.name} failed:\n{res.stderr.strip()}")


def build_service_points(cfg: CityConfig) -> gpd.GeoDataFrame:
    """Destinations from Overture places (+ GTFS stops for transit when configured), EPSG:4326."""
    places = gpd.read_parquet(cfg.raw_dir / "places.parquet")
    cls = classify_places(places, cfg.scoring, cfg.analysis_epsg)
    pts = cls[cls["status"] == ASSIGNED]
    pts = gpd.GeoDataFrame(
        {
            "service": pts["service"],
            "ref": pts["ref"],
            "dest_key": pts["id"],
            "name": pts["name"],
            "category": pts["taxonomy_primary"],
            "source": "overture_places",
            "secondary": pts["secondary"].astype(bool),
        },
        geometry=pts.geometry.values,
        crs=places.crs,
    )
    stops_path = cfg.raw_dir / "gtfs_stops.parquet"
    if cfg.scoring.services["transit"].prefer_gtfs and stops_path.exists():
        stops = gpd.read_parquet(stops_path).to_crs(places.crs)
        pts = pts[pts["service"] != "transit"]
        gtfs = gpd.GeoDataFrame(
            {
                "service": "transit",
                "ref": "gtfs:" + stops["stop_id"].astype(str),
                "dest_key": "gtfs:" + stops["stop_id"].astype(str),
                "name": stops["stop_name"],
                "category": "gtfs_stop",
                "source": "gtfs",
                "secondary": False,
            },
            geometry=stops.geometry.values,
            crs=places.crs,
        )
        pts = pd.concat([pts, gtfs], ignore_index=True)
    return gpd.GeoDataFrame(pts.reset_index(drop=True), geometry="geometry", crs=places.crs)


def build_block_groups(cfg: CityConfig) -> gpd.GeoDataFrame:
    """Scored block groups joined with ACS (one table, so no duplicate key columns in SQL)."""
    bg = gpd.read_parquet(cfg.raw_dir / "block_groups.parquet")
    acs = pd.read_parquet(cfg.raw_dir / "acs.parquet")
    bg = (
        bg[bg["scored"]].drop(columns=["scored"]).merge(acs, on="geoid", how="left", validate="1:1")
    )
    if bg["pop_total"].isna().any():
        raise ValueError("scored block group without ACS row")
    return bg


def load_all(cfg: CityConfig) -> dict[str, float]:
    """Create the schema and load every input table; returns seconds per table."""
    cfg.ensure_dirs()
    run_sql("00_schema.sql", cfg)
    timings: dict[str, float] = {}
    interim = {
        "block_groups": (build_block_groups(cfg), "PROMOTE_TO_MULTI"),
        "service_points": (build_service_points(cfg), "POINT"),
    }
    for table, (gdf, nlt) in interim.items():
        path = cfg.interim_dir / f"{table}.parquet"
        gdf.to_parquet(path)
        t0 = time.time()
        load_parquet(path, table, cfg, nlt)
        timings[table] = time.time() - t0
    for table, (fname, nlt) in TABLES.items():
        t0 = time.time()
        print(f"[load] {table} …", flush=True)
        load_parquet(cfg.raw_dir / fname, table, cfg, nlt)
        timings[table] = time.time() - t0
    run_sql("10_prepare.sql", cfg)
    return timings
