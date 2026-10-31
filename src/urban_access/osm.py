"""OpenStreetMap barriers (gates, fences) via the Overpass API — Overture has none.

Gates are cached as points, fences/walls/hedges as lines, in data/raw/<slug>/. Only gates that
explicitly close the way to walkers (access=no or foot=no, and no foot=yes/designated/permissive)
are used by the network (`closed`); everything else is a reference layer for QGIS.
OSM data © OpenStreetMap contributors, ODbL. OSM is live: the Overpass timestamp is recorded.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import geopandas as gpd
import requests
from shapely.geometry import LineString, Point

from .config import BBox, CityConfig

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
GATE_TYPES = (
    "gate|lift_gate|swing_gate|sliding_gate|kissing_gate|turnstile|full-height_turnstile|stile"
)
LINE_TYPES = "fence|wall|hedge|retaining_wall"
FOOT_OPEN = {"yes", "designated", "permissive"}
KEEP_TAGS = ("barrier", "access", "foot", "locked", "name", "opening_hours")


def query(bbox: BBox) -> str:
    s, w, n, e = bbox.lat_min, bbox.lon_min, bbox.lat_max, bbox.lon_max
    box = f"({s},{w},{n},{e})"
    return (
        f'[out:json][timeout:180];(node["barrier"~"^({GATE_TYPES})$"]{box};'
        f'way["barrier"~"^({LINE_TYPES})$"]{box};);out tags geom;'
    )


def is_closed(tags: dict[str, str]) -> bool:
    """True only when the gate is explicitly closed to walkers."""
    foot = tags.get("foot")
    if foot in FOOT_OPEN:
        return False
    return foot == "no" or tags.get("access") == "no"


def _frame(elements: list[dict]) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    pts, lines = [], []
    for el in elements:
        tags = el.get("tags", {})
        row = {
            "osm_id": f"{el['type']}/{el['id']}",
            **{k: tags.get(k) for k in KEEP_TAGS},
            "tags_json": json.dumps(tags, sort_keys=True),
        }
        if el["type"] == "node":
            pts.append(row | {"closed": is_closed(tags), "geometry": Point(el["lon"], el["lat"])})
        elif el["type"] == "way" and len(el.get("geometry", [])) >= 2:
            geom = LineString([(g["lon"], g["lat"]) for g in el["geometry"]])
            lines.append(row | {"geometry": geom})
    cols = ["osm_id", *KEEP_TAGS, "tags_json"]
    gates = gpd.GeoDataFrame(pts, columns=[*cols, "closed", "geometry"], crs=4326)
    fences = gpd.GeoDataFrame(lines, columns=[*cols, "geometry"], crs=4326)
    return gates, fences


def run(cfg: CityConfig, extent: BBox, force: bool = False) -> dict[str, Path]:
    """Write osm_gates.parquet and osm_fences.parquet (+ .meta.json) for the extent."""
    cfg.ensure_dirs()
    paths = {k: cfg.raw_dir / f"osm_{k}.parquet" for k in ("gates", "fences")}
    if not force and all(p.exists() for p in paths.values()):
        print("[osm] cached", *(p.name for p in paths.values()), flush=True)
        return paths
    print("[osm] Overpass: gates and fences …", flush=True)
    r = requests.post(
        OVERPASS_URL,
        data={"data": query(extent)},
        headers={"User-Agent": "urban-accessibility (github.com/xoffvsg/urban-accessibility)"},
        timeout=300,
    )
    r.raise_for_status()
    data = r.json()
    gates, fences = _frame(data["elements"])
    meta = {
        "source": OVERPASS_URL,
        "osm_base": data.get("osm3s", {}).get("timestamp_osm_base"),
        "extent": extent.as_tuple(),
        "license": "ODbL, © OpenStreetMap contributors",
        "created_utc": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    for (k, gdf), p in zip({"gates": gates, "fences": fences}.items(), paths.values(), strict=True):
        gdf.to_parquet(p)
        p.with_suffix(".meta.json").write_text(json.dumps(meta | {"rows": len(gdf)}, indent=2))
        print(f"[osm] {k}: {len(gdf)} rows", flush=True)
    return paths
