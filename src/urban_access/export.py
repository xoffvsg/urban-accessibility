"""Web export: block groups, services and parks → PMTiles (tippecanoe) + summary JSON for docs/."""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pygris
from sqlalchemy import text

from . import db
from .config import REPO_ROOT, CityConfig, Scoring

DOCS_DATA = REPO_ROOT / "docs" / "data"
ATTRIBUTION = [
    "Places: Overture Maps Foundation (CDLA-Permissive-2.0)",
    "Buildings, roads, land use: Overture Maps / OpenStreetMap contributors (ODbL)",
    "Gates and fences: © OpenStreetMap contributors (ODbL)",
    "Block groups and ACS: US Census Bureau",
    "Transit stops: Jump Around Carson (GTFS)",
    "Service checks: NCES, USDA SNAP, CMS NPPES, HRSA, US Census Geocoder",
]
BASEMAP_ATTRIBUTION = "Basemap: OpenFreeMap © OpenMapTiles © OpenStreetMap contributors"
# ACS estimate → (MOE column for the badge's CV, count column the CV is computed from)
CV_FIELDS = {
    "median_hh_income": "median_hh_income_cv",
    "zero_car_hh_pct": "zero_car_hh_pct_cv",
    "below_pov_pct": "below_pov_pct_cv",
    "pop_65plus_pct": None,  # derived below from the 65+ count
}


def _cv(est: pd.Series, moe: pd.Series) -> pd.Series:
    return 100 * (moe / 1.645) / est.where(est > 0)


def block_group_features(
    bg: gpd.GeoDataFrame, equity: pd.DataFrame, scoring: Scoring, counties: dict[str, str]
) -> gpd.GeoDataFrame:
    """Popup / styling properties only (EPSG:4326), one feature per block group."""
    cv_limit = float(scoring.web.get("cv_flag_pct", 30))
    d = bg.merge(equity, on="geoid", how="left", validate="1:1")
    out = pd.DataFrame(
        {
            "geoid": d["geoid"],
            "label": (
                "Tract "
                + (d["tractce"].astype(int) / 100).map("{:g}".format)
                + ", BG "
                + d["blkgrpce"].astype(str)
                + " ("
                + (d["statefp"] + d["countyfp"]).map(counties).fillna("")
                + ")"
            ),  # fmt: skip
            "pop_total": d["pop_total"],
            "pop_total_moe": d["pop_total_moe"],
            "access_score": d["access_score"],
            "access_rank": d["access_rank"],
            "access_pctile": d["access_pctile"],
        }
    )
    for s in scoring.scored_services:
        out[f"{s}_score"] = d[f"{s}_score"]
        out[f"{s}_pop_5min_pct"] = d[f"{s}_pop_5min_pct"]
        out[f"{s}_dist_centroid_m"] = d[f"{s}_dist_centroid_m"]
    out["grocery_n_400m"] = d["grocery_n_400m"]
    out["people_per_grocery"] = d["people_per_grocery"]
    for c in ("median_hh_income", "zero_car_hh_pct", "below_pov_pct", "pop_65plus_pct"):
        out[c] = d[c]
    out["median_hh_income_moe"] = d["median_hh_income_moe"]
    cv65 = _cv(d["pop_65plus"], d["pop_65plus_moe"])
    for c, cvcol in CV_FIELDS.items():
        cv = cv65 if cvcol is None else d[cvcol]
        out[f"{c}_uncertain"] = (cv > cv_limit).fillna(False)
    out["lisa_cluster"] = d["lisa_cluster"].fillna("")
    out["income_tercile"] = d["income_tercile"]
    out["has_residents"] = d["pop_total"] > 0
    num = out.select_dtypes("number").columns
    out[num] = out[num].round(1)
    return gpd.GeoDataFrame(out, geometry=d.geometry.values, crs=bg.crs).to_crs(4326)


def service_features(dests: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Service points (park entrance points generated from polygons are left out)."""
    s = dests[dests["source"] != "landuse_access"]
    out = gpd.GeoDataFrame(
        {"service": s["service"], "name": s["name"].fillna(""), "secondary": s["secondary"]},
        geometry=s.geometry.values,
        crs=dests.crs,
    )
    return out.to_crs(4326)


def summary(
    feats: gpd.GeoDataFrame, cfg: CityConfig, equity_summary: dict | None
) -> dict[str, object]:
    sc = cfg.scoring
    rows = feats.drop(columns="geometry")
    keep = ["geoid", "label", "pop_total", "access_score"] + [
        f"{s}_score" for s in sc.scored_services
    ]
    xmin, ymin, xmax, ymax = feats.total_bounds
    headline = (equity_summary or {}).get("headline", [])
    return {
        "city": cfg.name,
        "slug": cfg.slug,
        "bounds": [round(v, 5) for v in (xmin, ymin, xmax, ymax)],
        "overture_release": cfg.overture_release,
        "acs_year": cfg.acs_year,
        "weights": sc.weights,
        "services": sc.scored_services,
        "bands": [{"max_m": m, "value": v} for m, v in sc.bands],
        "web": sc.web,
        "attribution": ATTRIBUTION
        + (
            [f"Some park areas: {cfg.xcheck.get('city_parks_source', 'city GIS')}"]
            if cfg.extra_parks
            else []
        )
        + ([f"Homes: {'; '.join(h['source'] for h in cfg.homes)}"] if cfg.homes else []),
        "basemap_attribution": BASEMAP_ATTRIBUTION,
        "headline": [
            {k: (round(v, 1) if isinstance(v, float) else v) for k, v in h.items()}
            for h in headline
        ],
        "block_groups": [
            r | {"bbox": [round(v, 5) for v in b]}
            for r, b in zip(
                json.loads(
                    rows[keep].replace({np.nan: None}).to_json(orient="records", double_precision=1)
                ),
                feats.geometry.bounds.to_numpy(),
                strict=True,
            )
        ],
        "created_utc": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def write_tiles(layers: dict[str, gpd.GeoDataFrame], out: Path, work: Path) -> None:
    """GeoJSON per layer (6-decimal coordinates) → one PMTiles file with tippecanoe."""
    if shutil.which("tippecanoe") is None:
        raise RuntimeError("tippecanoe not on PATH (conda env urban-access)")
    work.mkdir(parents=True, exist_ok=True)
    cmd = ["tippecanoe", "-o", str(out), "--force", "-Z9", "-z15", "--detect-shared-borders",
           "--no-tile-size-limit", "--no-feature-limit", "-r1", "--quiet"]  # fmt: skip
    for name, gdf in layers.items():
        path = work / f"{name}.geojson"
        gdf.to_file(path, driver="GeoJSON", layer_options={"COORDINATE_PRECISION": 6})
        cmd += ["-L", f"{name}:{path}"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"tippecanoe failed:\n{res.stderr.strip()}")


def county_names(geoids: list[str], year: int) -> dict[str, str]:
    """5-digit county GEOID → name ("Carson City", "Douglas County")."""
    c = pygris.counties(cb=True, year=year, cache=True)
    c = c[c["GEOID"].isin(set(geoids))]
    return dict(zip(c["GEOID"], c["NAMELSAD"].str.replace(" city$", "", regex=True), strict=True))


def run(cfg: CityConfig) -> dict[str, Path]:
    """Write docs/data/<slug>.pmtiles and docs/data/<slug>_summary.json."""
    print("[export] reading tables …", flush=True)
    with db.engine().connect() as con:
        bg = gpd.read_postgis(text(f'SELECT * FROM "{cfg.slug}".bg_scores'), con, geom_col="geom")
        dests = gpd.read_postgis(
            text(f'SELECT service, name, secondary, source, geom FROM "{cfg.slug}".dests'),
            con,
            geom_col="geom",
        )
        parks = gpd.read_postgis(
            text(f'SELECT name, geom FROM "{cfg.slug}".parks'), con, geom_col="geom"
        )
        eq = pd.read_sql(
            text(f'SELECT geoid, lisa_cluster, income_tercile FROM "{cfg.slug}".bg_equity'), con
        )
    counties = county_names(sorted({g[:5] for g in bg["geoid"]}), cfg.acs_year)
    feats = block_group_features(bg, eq, cfg.scoring, counties)
    layers = {
        "block_groups": feats,
        "services": service_features(dests),
        "parks": parks.assign(name=parks["name"].fillna("")).to_crs(4326),
    }
    DOCS_DATA.mkdir(parents=True, exist_ok=True)
    tiles = DOCS_DATA / f"{cfg.slug}.pmtiles"
    print("[export] tippecanoe …", flush=True)
    write_tiles(layers, tiles, cfg.interim_dir / "web")

    eq_path = cfg.processed_dir / "equity_summary.json"
    eq_sum = json.loads(eq_path.read_text()) if eq_path.exists() else None
    summ = DOCS_DATA / f"{cfg.slug}_summary.json"
    summ.write_text(json.dumps(summary(feats, cfg, eq_sum), indent=1) + "\n")
    for p in (tiles, summ):
        print(f"[export] {p.relative_to(REPO_ROOT)}: {p.stat().st_size / 1024:.0f} KB", flush=True)
    return {"tiles": tiles, "summary": summ}
