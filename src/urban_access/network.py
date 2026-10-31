"""Walk network and distance computation in PostGIS/pgRouting (logic lives in sql/)."""

from __future__ import annotations

import json
import warnings

import geopandas as gpd
import pandas as pd
from sqlalchemy import text

from . import db
from .config import CityConfig


def build(cfg: CityConfig) -> pd.Series:
    """Walkable segments → edges/vertices → components (sql/20_network.sql); returns stats."""
    print("[network] building walk network …", flush=True)
    write_blocked_segments(cfg)
    write_gated_areas(cfg)
    write_gates(cfg)
    db.run_sql("20_network.sql", cfg)
    return stats(cfg)


def write_blocked_segments(cfg: CityConfig) -> None:
    """City `blocked_segments:` → <slug>.blocked_segments, read by sql/20_network.sql."""
    rows = pd.DataFrame(
        list(cfg.blocked_segments.items()), columns=["segment_id", "note"], dtype=str
    )
    with db.engine().begin() as con:
        rows.to_sql("blocked_segments", con, schema=cfg.slug, if_exists="replace", index=False)
        missing = (
            con.execute(
                text(
                    f'SELECT b.segment_id FROM "{cfg.slug}".blocked_segments b '
                    f'LEFT JOIN "{cfg.slug}".segments s ON s.id = b.segment_id WHERE s.id IS NULL'
                )
            )
            .scalars()
            .all()
        )
    for sid in missing:
        warnings.warn(f"blocked_segments: no segment with id {sid!r}", stacklevel=2)


def write_gated_areas(cfg: CityConfig) -> None:
    """City `gated_areas:` GeoJSON → <slug>.gated_areas (exits = street names joined by ';')."""
    q = f"""
        DROP TABLE IF EXISTS "{cfg.slug}".gated_areas;
        CREATE TABLE "{cfg.slug}".gated_areas (
            name text PRIMARY KEY, exits text NOT NULL DEFAULT '', note text,
            geom geometry(MultiPolygon, {cfg.analysis_epsg}) NOT NULL);
    """
    rows = []
    if cfg.gated_areas_path is not None:
        gdf = gpd.read_file(cfg.gated_areas_path).to_crs(cfg.analysis_epsg)
        for r in gdf.itertuples():
            exits = json.loads(r.exits) if isinstance(r.exits, str) else list(r.exits)
            rows.append(
                {"name": r.name, "exits": ";".join(exits), "note": r.note, "wkt": r.geometry.wkt}
            )
    with db.engine().begin() as con:
        con.execute(text(q))
        for row in rows:
            con.execute(
                text(
                    f'INSERT INTO "{cfg.slug}".gated_areas (name, exits, note, geom) '
                    f"VALUES (:name, :exits, :note, "
                    f"ST_Multi(ST_GeomFromText(:wkt, {cfg.analysis_epsg})))"
                ),
                row,
            )


def write_gates(cfg: CityConfig) -> None:
    """City `gates:` GeoJSON (lines across gates, status open|closed) → <slug>.gates."""
    q = f"""
        DROP TABLE IF EXISTS "{cfg.slug}".gates;
        CREATE TABLE "{cfg.slug}".gates (
            id serial PRIMARY KEY, status text NOT NULL CHECK (status IN ('open', 'closed')),
            note text, geom geometry(LineString, {cfg.analysis_epsg}) NOT NULL);
    """
    rows = []
    if cfg.gates_path is not None:
        gdf = gpd.read_file(cfg.gates_path).to_crs(cfg.analysis_epsg)
        for r in gdf.itertuples():
            rows.append({"status": r.status, "note": r.note, "wkt": r.geometry.wkt})
    with db.engine().begin() as con:
        con.execute(text(q))
        for row in rows:
            con.execute(
                text(
                    f'INSERT INTO "{cfg.slug}".gates (status, note, geom) '
                    f"VALUES (:status, :note, ST_GeomFromText(:wkt, {cfg.analysis_epsg}))"
                ),
                row,
            )


def stats(cfg: CityConfig) -> pd.Series:
    q = f"""
        SELECT count(*) AS edges,
               count(*) FILTER (WHERE main) AS main_edges,
               sum(cost_m) / 1000 AS length_km,
               100 * sum(cost_m) FILTER (WHERE main) / sum(cost_m) AS main_length_pct,
               (SELECT count(*) FROM "{cfg.slug}".vertices) AS vertices,
               (SELECT count(DISTINCT component) FROM "{cfg.slug}".vertices) AS components
        FROM "{cfg.slug}".edges
    """
    with db.engine().connect() as con:
        return pd.read_sql(text(q), con).iloc[0]


def distances(cfg: CityConfig) -> None:
    """Origins (30), destinations (40), network distances + counts + service areas (50)."""
    s = cfg.scoring
    snap = s.max_snap_distance_m
    r, n = s.residential, s.network
    print("[network] origins …", flush=True)
    db.run_sql(
        "30_origins.sql",
        cfg,
        min_area_m2=r["unclassified_min_area_m2"],
        max_area_m2=r["unclassified_max_area_m2"],
        floor_height_m=r["floor_height_m"],
        grid_m=r["fallback_grid_m"],
        max_snap_m=snap,
    )
    print("[network] destinations …", flush=True)
    db.run_sql(
        "40_destinations.sql",
        cfg,
        park_step_m=n["park_access_step_m"],
        park_access_m=n["park_access_max_m"],
        max_snap_m=snap,
    )
    print("[network] distances …", flush=True)
    db.run_sql(
        "50_distances.sql",
        cfg,
        max_dist_m=s.max_m,
        near_m=s.near_m,
        people_radius_m=s.max_m,
        sa_buffer_m=n["service_area_buffer_m"],
    )


def check_pairs(cfg: CityConfig, n: int = 5, seed: float = 0.42) -> pd.DataFrame:
    """Independent check: one-to-many pgr_dijkstraCost (origin inserted as its own virtual
    node) for `n` random origin/service pairs, compared with origin_dist.dist_m."""
    with db.engine().begin() as con:
        con.execute(text("SELECT setseed(:s)"), {"s": seed})
        return pd.read_sql(
            text((db.SQL_DIR / "90_check_pairs.sql").read_text().replace("__SCHEMA__", cfg.slug)),
            con,
            params={"n": n},
        )
