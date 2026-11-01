"""Web export: block groups, services and parks → PMTiles (tippecanoe) + summary JSON for docs/."""

from __future__ import annotations

import json
import re
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
    "Block groups and American Community Survey (ACS): US Census Bureau",
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


STREET_SUFFIX = {
    "street": "St", "drive": "Dr", "road": "Rd", "avenue": "Ave", "parkway": "Pkwy",
    "lane": "Ln", "boulevard": "Blvd", "highway": "Hwy", "court": "Ct", "circle": "Cir",
    "place": "Pl", "trail": "Trl",
}  # fmt: skip
DIRECTION = {"north": "N", "south": "S", "east": "E", "west": "W"}
ARTERIALS = ("trunk", "primary", "secondary", "tertiary")
PARK_INSIDE_SHARE = 0.5  # a park names a neighborhood only if at least half of it lies inside


def short_street(name: str) -> str:
    """'South Carson Street' → 'S Carson St' (direction prefix and suffix abbreviated)."""
    words = name.split()
    if len(words) > 1 and words[0].lower() in DIRECTION:
        words[0] = DIRECTION[words[0].lower()]
    if len(words) > 1 and words[-1].lower() in STREET_SUFFIX:
        words[-1] = STREET_SUFFIX[words[-1].lower()]
    return " ".join(words)


def tract_bg(tractce: str, blkgrpce: str) -> str:
    """Census tract + block group as people write it: '001001' + '2' → '10.01-2'."""
    return f"{int(tractce) / 100:g}-{blkgrpce}"


def area_names(
    bg: pd.DataFrame,
    parks: pd.DataFrame,
    roads: pd.DataFrame,
    overrides: dict[str, str] | None = None,
    skip: list[str] | None = None,
) -> pd.DataFrame:
    """Plain-language neighborhood name per block group → DataFrame(geoid, name, source).

    bg: geoid, tractce, blkgrpce, lat (centroid, for ordering duplicates).
    parks: geoid, name, inside_m2 (park area inside the BG), park_m2 (whole park).
    roads: geoid, name, class, length_m (named street length inside the BG).
    A park that lies at least half inside names the BG ("Mills Park area"), largest first; else the
    two longest arterials ("Fairview Dr / Saliman Rd"), else the longest street; names are made
    unique; city config overrides (`area_names:`) win. Park names containing a `skip` word (trails,
    open-space parcels) are not used.
    """

    def streets(geoid: str) -> str | None:
        r = roads[roads["geoid"] == geoid]
        if r.empty:
            return None
        art = r[r["class"].isin(ARTERIALS)]
        pick = art if not art.empty else r
        by = pick.assign(n=pick["name"].map(short_street)).groupby("n")["length_m"].sum()
        top = by.sort_values(ascending=False).index[: 2 if not art.empty else 1]
        return " / ".join(top)

    def park_name(n: str) -> str:
        return n if n.lower().endswith(" area") else f"{n} area"

    ok = parks[(parks["inside_m2"] >= PARK_INSIDE_SHARE * parks["park_m2"]) & parks["name"].ne("")]
    if skip:
        pat = "|".join(re.escape(w) for w in skip)
        ok = ok[~ok["name"].str.contains(pat, case=False)]
    best = ok.sort_values("inside_m2", ascending=False).drop_duplicates("geoid").set_index("geoid")
    rows = []
    for r in bg.itertuples():
        fallback = streets(r.geoid) or f"Neighborhood {tract_bg(r.tractce, r.blkgrpce)}"
        if r.geoid in best.index:
            rows.append((r.geoid, park_name(best.at[r.geoid, "name"]), "park",
                         best.at[r.geoid, "inside_m2"], fallback, r.lat))  # fmt: skip
        else:
            rows.append((r.geoid, fallback, "streets", 0.0, fallback, r.lat))
    out = pd.DataFrame(rows, columns=["geoid", "name", "source", "weight", "fallback", "lat"])
    # a park name used twice stays with the BG holding more of it; the other takes its streets
    dup = out.sort_values("weight", ascending=False).duplicated("name") & (out["source"] == "park")
    out.loc[dup, ["name", "source"]] = out.loc[dup, ["fallback"]].assign(s="streets").to_numpy()
    # still duplicated (same two streets): north … south by centroid
    for _, grp in out[out.duplicated("name", keep=False)].groupby("name"):
        order = grp.sort_values("lat", ascending=False).index
        tags = ["north", "south"] if len(order) == 2 else [str(i + 1) for i in range(len(order))]
        for i, tag in zip(order, tags, strict=True):
            out.loc[i, "name"] = f"{out.loc[i, 'name']} ({tag})"
    for g, n in (overrides or {}).items():
        out.loc[out["geoid"] == g, ["name", "source"]] = [n, "override"]
    return out[["geoid", "name", "source"]]


def block_group_features(
    bg: gpd.GeoDataFrame,
    equity: pd.DataFrame,
    scoring: Scoring,
    counties: dict[str, str],
    names: pd.Series | None = None,
) -> gpd.GeoDataFrame:
    """Popup / styling properties only (EPSG:4326), one feature per block group.

    `names`: neighborhood name by geoid (`area_names`); default "Neighborhood <tract>-<bg>".
    """
    cv_limit = float(scoring.web.get("cv_flag_pct", 30))
    d = bg.merge(equity, on="geoid", how="left", validate="1:1")
    tbg = [tract_bg(t, b) for t, b in zip(d["tractce"], d["blkgrpce"], strict=True)]
    county = (d["statefp"] + d["countyfp"]).map(counties).fillna("")
    label = pd.Series(["Neighborhood " + x for x in tbg], index=d.index)
    if names is not None:
        label = d["geoid"].map(names).fillna(label)
    out = pd.DataFrame(
        {
            "geoid": d["geoid"],
            "label": label,
            "census_label": ["Census block group " + x for x in tbg]
            + (", " + county).where(county != "", ""),
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


def service_locations(services: gpd.GeoDataFrame) -> list[dict[str, object]]:
    """Service points grouped by location (EPSG:4326, 6 decimals): one entry per spot with every
    distinct (service, name) there, so the map can draw a split dot where services share a spot."""
    pts = services.assign(
        lon=services.geometry.x.round(6), lat=services.geometry.y.round(6)
    ).drop_duplicates(["lon", "lat", "service", "name"])
    out = []
    for (lon, lat), g in pts.groupby(["lon", "lat"], sort=True):
        items = [{"service": r.service, "name": r.name} for r in g.itertuples()]
        out.append({"lon": float(lon), "lat": float(lat), "items": items})
    return out


def summary(
    feats: gpd.GeoDataFrame,
    cfg: CityConfig,
    equity_summary: dict | None,
    services: gpd.GeoDataFrame | None = None,
) -> dict[str, object]:
    sc = cfg.scoring
    rows = feats.drop(columns="geometry")
    keep = ["geoid", "label", "census_label", "pop_total", "access_score"] + [
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
        "service_points": [] if services is None else service_locations(services),
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


PARKS_IN_BG_SQL = """
SELECT g.geoid, p.name, ST_Area(ST_Intersection(p.geom, g.geom)) AS inside_m2,
       ST_Area(p.geom) AS park_m2
FROM __S__.bg_scores g JOIN __S__.parks p ON ST_Intersects(p.geom, g.geom)
WHERE coalesce(p.name, '') <> ''
"""
ROADS_IN_BG_SQL = """
SELECT g.geoid, s.name, s.class, sum(ST_Length(ST_Intersection(s.geom, g.geom))) AS length_m
FROM __S__.bg_scores g JOIN __S__.segments s ON ST_Intersects(s.geom, g.geom)
WHERE coalesce(s.name, '') <> '' AND s.class IS DISTINCT FROM 'motorway'
GROUP BY 1, 2, 3
"""


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
        parks_in = pd.read_sql(text(PARKS_IN_BG_SQL.replace("__S__", f'"{cfg.slug}"')), con)
        roads_in = pd.read_sql(text(ROADS_IN_BG_SQL.replace("__S__", f'"{cfg.slug}"')), con)
    counties = county_names(sorted({g[:5] for g in bg["geoid"]}), cfg.acs_year)
    lat = bg.to_crs(4326).geometry.representative_point().y
    names = area_names(
        bg[["geoid", "tractce", "blkgrpce"]].assign(lat=lat.values), parks_in, roads_in,
        cfg.area_names, cfg.scoring.web.get("area_name_skip"),
    )  # fmt: skip
    names.to_csv(cfg.interim_dir / "area_names.csv", index=False)
    print("[export] neighborhood names (" + ", ".join(
        f"{k} {v}" for k, v in names["source"].value_counts().items()) + "):")  # fmt: skip
    for r in names.itertuples():
        print(f"  {r.geoid}  {r.name}  [{r.source}]")
    feats = block_group_features(bg, eq, cfg.scoring, counties, names.set_index("geoid")["name"])
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
    summ.write_text(json.dumps(summary(feats, cfg, eq_sum, layers["services"]), indent=1) + "\n")
    for p in (tiles, summ):
        print(f"[export] {p.relative_to(REPO_ROOT)}: {p.stat().st_size / 1024:.0f} KB", flush=True)
    return {"tiles": tiles, "summary": summ}
