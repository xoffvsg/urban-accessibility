"""Walk network and distance computation in PostGIS/pgRouting (logic lives in sql/)."""

from __future__ import annotations

import pandas as pd
from sqlalchemy import text

from . import db
from .config import CityConfig


def build(cfg: CityConfig) -> pd.Series:
    """Walkable segments → edges/vertices → components (sql/20_network.sql); returns stats."""
    print("[network] building walk network …", flush=True)
    db.run_sql("20_network.sql", cfg)
    return stats(cfg)


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
