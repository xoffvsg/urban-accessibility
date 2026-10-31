"""Minimal ArcGIS REST feature-layer query (envelope filter, paging) → GeoDataFrame in EPSG:4326."""

from __future__ import annotations

import geopandas as gpd
import pandas as pd
import requests

from .config import BBox

PAGE = 1000


def _get(url: str, params: dict[str, str]) -> dict:
    r = requests.get(url, params=params, timeout=180)
    r.raise_for_status()
    d = r.json()
    if "error" in d:
        raise RuntimeError(f"ArcGIS {url}: {d['error']}")
    return d


def query(
    layer_url: str,
    *,
    extent: BBox | None = None,
    where: str = "1=1",
    out_fields: str = "*",
    geometry: bool = True,
) -> gpd.GeoDataFrame | pd.DataFrame:
    """All features of `layer_url` (…/FeatureServer/0) matching `where` and the WGS84 `extent`."""
    params = {
        "where": where,
        "outFields": out_fields,
        "outSR": "4326",
        "resultRecordCount": str(PAGE),
    }
    if extent is not None:
        params |= {
            "geometry": f"{extent.lon_min},{extent.lat_min},{extent.lon_max},{extent.lat_max}",
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
        }
    features: list[dict] = []
    offset = 0
    while True:
        page = {**params, "resultOffset": str(offset)}
        if geometry:
            d = _get(f"{layer_url}/query", page | {"f": "geojson"})
        else:
            d = _get(f"{layer_url}/query", page | {"f": "json", "returnGeometry": "false"})
        batch = d.get("features", [])
        features += batch
        more = d.get("exceededTransferLimit") or d.get("properties", {}).get(
            "exceededTransferLimit"
        )
        if not more or not batch:
            break
        offset += len(batch)
    if not geometry:
        return pd.DataFrame([f["attributes"] for f in features])
    if not features:
        return gpd.GeoDataFrame(geometry=[], crs=4326)
    return gpd.GeoDataFrame.from_features(features, crs=4326)
