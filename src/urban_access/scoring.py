"""Band scores, per-service block-group metrics and the composite access score."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime

import geopandas as gpd
import numpy as np
import pandas as pd
from sqlalchemy import text

from . import db
from .config import CityConfig, Scoring


def band_value(dist_m: Sequence[float] | np.ndarray | pd.Series, bands) -> np.ndarray:
    """Access value per distance: value of the first band with dist <= max_m; else / NaN → 0."""
    d = np.asarray(dist_m, dtype=float)
    out = np.zeros_like(d)
    done = np.isnan(d)
    for max_m, value in bands:
        hit = ~done & (d <= max_m)
        out[hit] = value
        done |= hit
    return out


def edge_point_distance(
    snap_m: float, frac: float, edge_len_m: float, d_source: float, d_target: float
) -> float:
    """Network distance from a point on an edge (at `frac`, `snap_m` off the edge) to the nearest
    destination, given the destination distances of the edge's two ends (as in 50_distances.sql).
    """
    return snap_m + min(d_source + frac * edge_len_m, d_target + (1 - frac) * edge_len_m)


def _wmean(values: pd.Series, weights: pd.Series) -> float:
    w = weights.sum()
    return float((values * weights).sum() / w) if w > 0 else np.nan


def score_block_groups(
    od: pd.DataFrame,
    origins: pd.DataFrame,
    bg: pd.DataFrame,
    counts: pd.DataFrame,
    cover: pd.DataFrame,
    scoring: Scoring,
) -> pd.DataFrame:
    """One row per block group (index geoid) with per-service metrics and the composite score.

    od: origin_id, service, dist_m, euclid_m · origins: id, kind, bg_geoid, pop, snap_m ·
    bg: geoid, pop_total · counts: bg_geoid, service, n_near, n_people_radius ·
    cover: bg_geoid, service, cover_pct.
    """
    near = scoring.near_m
    near_tag = f"{near:g}m"
    res = origins[origins["kind"] != "centroid"].set_index("id")
    cen = origins[origins["kind"] == "centroid"].set_index("id")

    d = od.merge(res[["bg_geoid", "pop"]], left_on="origin_id", right_index=True)
    d["band"] = band_value(d["dist_m"], scoring.bands)
    d["near"] = (d["dist_m"] <= near).astype(float)

    out = bg.set_index("geoid")[["pop_total"]].copy()
    services = sorted(od["service"].unique())
    for s in services:
        ds = d[d["service"] == s]
        g = ds.groupby("bg_geoid")
        out[f"{s}_score"] = 100 * g.apply(
            lambda x: _wmean(x["band"], x["pop"]), include_groups=False
        )
        out[f"{s}_pop_5min_pct"] = 100 * g.apply(
            lambda x: _wmean(x["near"], x["pop"]), include_groups=False
        )
        c = counts[counts["service"] == s].set_index("bg_geoid")
        out[f"{s}_n_{near_tag}"] = c["n_near"].reindex(out.index).fillna(0).astype(int)
        cv = cover[cover["service"] == s].set_index("bg_geoid")["cover_pct"]
        out[f"{s}_cover_{near_tag}_pct"] = cv.reindex(out.index).fillna(0.0)
        dc = od[(od["service"] == s) & od["origin_id"].isin(cen.index)]
        dc = dc.merge(cen[["bg_geoid"]], left_on="origin_id", right_index=True)
        out[f"{s}_dist_centroid_m"] = dc.set_index("bg_geoid")["dist_m"].reindex(out.index)

    # zero-population block groups get NaN scores (no residents to score)
    nopop = out["pop_total"] <= 0
    score_cols = [c for c in out if c.endswith("_score") or c.endswith("_pop_5min_pct")]
    out.loc[nopop, score_cols] = np.nan

    g = counts[counts["service"] == "grocery"].set_index("bg_geoid")["n_people_radius"]
    n_groc = g.reindex(out.index).fillna(0)
    out["groceries_within_max_m"] = n_groc.astype(int)
    out["people_per_grocery"] = (out["pop_total"] / n_groc).where(n_groc > 0)

    w = scoring.weights
    out["access_score"] = sum(w[s] * out[f"{s}_score"] for s in scoring.scored_services)
    out["access_rank"] = out["access_score"].rank(ascending=False, method="min").astype("Int64")
    out["access_pctile"] = 100 * out["access_score"].rank(pct=True)

    # diagnostics: circuity (network / straight line, pop-weighted, reachable pairs only)
    ok = d["dist_m"].notna() & (d["euclid_m"] > 0)
    d.loc[ok, "circuity"] = d.loc[ok, "dist_m"] / d.loc[ok, "euclid_m"]
    out["circuity_ratio"] = (
        d[ok]
        .groupby("bg_geoid")
        .apply(lambda x: _wmean(x["circuity"], x["pop"]), include_groups=False)
    )
    snap = res.assign(flag=(res["snap_m"] > scoring.max_snap_distance_m).astype(float))
    out["snap_flag_pop_pct"] = 100 * snap.groupby("bg_geoid").apply(
        lambda x: _wmean(x["flag"], x["pop"]), include_groups=False
    )
    out["n_origins"] = res.groupby("bg_geoid").size().reindex(out.index).fillna(0).astype(int)
    return out.drop(columns="pop_total")


def _read(cfg: CityConfig, q: str) -> pd.DataFrame:
    with db.engine().connect() as con:
        return pd.read_sql(text(q.replace("__SCHEMA__", cfg.slug)), con)


def run(cfg: CityConfig) -> gpd.GeoDataFrame:
    """Score every block group; write <slug>.bg_scores and data/processed/<slug>/ outputs."""
    print("[score] reading distances …", flush=True)
    od = _read(cfg, "SELECT origin_id, service, dist_m, euclid_m FROM __SCHEMA__.origin_dist")
    origins = _read(cfg, "SELECT id, kind, bg_geoid, pop, snap_m FROM __SCHEMA__.origins")
    counts = _read(cfg, "SELECT * FROM __SCHEMA__.bg_counts")
    cover = _read(cfg, "SELECT * FROM __SCHEMA__.bg_cover")
    with db.engine().connect() as con:
        bg = gpd.read_postgis(
            text(f'SELECT * FROM "{cfg.slug}".block_groups'), con, geom_col="geom"
        ).drop(columns=["fid"])

    scores = score_block_groups(od, origins, bg, counts, cover, cfg.scoring)
    out = bg.merge(scores, left_on="geoid", right_index=True, how="left", validate="1:1")
    out["overture_release"] = cfg.overture_release
    out["acs_year"] = cfg.acs_year

    print("[score] writing bg_scores …", flush=True)
    with db.engine().begin() as con:
        out.to_postgis("bg_scores", con, schema=cfg.slug, if_exists="replace", index=False)

    cfg.ensure_dirs()
    wgs = out.to_crs(4326)
    wgs.to_parquet(cfg.processed_dir / "block_groups_scored.parquet")
    wgs.to_file(cfg.processed_dir / "block_groups_scored.geojson", driver="GeoJSON")
    meta = {
        "city": cfg.name,
        "overture_release": cfg.overture_release,
        "acs_year": cfg.acs_year,
        "analysis_epsg": cfg.analysis_epsg,
        "bands": cfg.scoring.bands,
        "weights": cfg.scoring.weights,
        "block_groups": len(out),
        "created_utc": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    (cfg.processed_dir / "block_groups_scored.meta.json").write_text(json.dumps(meta, indent=2))
    return out
