"""Open reference data for the cross-checks (xcheck.py) → data/raw/<slug>/*.parquet (+ .meta.json).

- SNAP authorized retailers (USDA FNS, live ArcGIS layer; public domain).
- City park polygons (Carson City GIS ArcGIS layer; no licence stated, used with attribution).
- CMS NPPES organization NPIs (NPI Registry API; public), geocoded with the Census Geocoder batch
  service (public domain), and HRSA health center sites (data.hrsa.gov; public domain).
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests

from . import arcgis
from .census import write_meta
from .config import BBox, CityConfig

NPPES_PAGE = 200
NPPES_MAX_SKIP = 1000  # API limit; a postal code with more organizations needs a finer split


def _inside(gdf: gpd.GeoDataFrame, extent: BBox) -> gpd.GeoDataFrame:
    x, y = gdf.geometry.x, gdf.geometry.y
    keep = x.between(extent.lon_min, extent.lon_max) & y.between(extent.lat_min, extent.lat_max)
    return gdf[keep].reset_index(drop=True)


def snap(cfg: CityConfig, extent: BBox, force: bool = False) -> Path:
    out = cfg.raw_dir / "snap_retailers.parquet"
    if out.exists() and not force:
        return out
    url = cfg.scoring.xcheck["grocery"]["url"]
    g = arcgis.query(url, extent=extent)
    g = g.rename(
        columns={"Record_ID": "source_id", "Store_Name": "name", "Store_Type": "store_type"}
    )
    g["address"] = g["Store_Street_Address"].fillna("") + ", " + g["City"].fillna("")
    g = g[["source_id", "name", "store_type", "address", "geometry"]]
    g.to_parquet(out)
    write_meta(
        out,
        source="USDA FNS SNAP authorized retailers",
        url=url,
        rows=len(g),
        licence="public domain (USDA)",
    )
    print(f"[snap] {len(g)} retailers")
    return out


def city_parks(cfg: CityConfig, force: bool = False) -> Path | None:
    url = cfg.xcheck.get("city_parks_url")
    if not url:
        return None
    out = cfg.raw_dir / "city_parks.parquet"
    if out.exists() and not force:
        return out
    g = arcgis.query(url)
    name = g["LABEL"].fillna(g["PARK_NAME"]) if "LABEL" in g else g["PARK_NAME"]
    g = gpd.GeoDataFrame(
        {"source_id": g["OBJECTID"].astype(str), "name": name, "category": g["CATEGORY"]},
        geometry=g.geometry.make_valid(),
        crs=4326,
    )
    g.to_parquet(out)
    write_meta(
        out,
        source=cfg.xcheck.get("city_parks_source", "city GIS"),
        url=url,
        rows=len(g),
        licence="not stated; used with attribution",
    )
    print(f"[city_parks] {len(g)} polygons")
    return out


# ---- healthcare -------------------------------------------------------------------------------


def nppes_rows(results: list[dict]) -> list[dict]:
    """NPI Registry results → one row per organization: names, primary taxonomy, location."""
    rows = []
    for r in results:
        loc = next(
            (a for a in r.get("addresses", []) if a.get("address_purpose") == "LOCATION"), None
        )
        if loc is None:
            continue
        tax = (
            next((t for t in r.get("taxonomies", []) if t.get("primary")), None)
            or (r.get("taxonomies") or [{}])[0]
        )
        others = [
            o.get("organization_name")
            for o in r.get("other_names", [])
            if o.get("organization_name")
        ]
        rows.append(
            {
                "source_id": str(r["number"]),
                "name": r.get("basic", {}).get("organization_name", ""),
                "other_names": "; ".join(others),
                "taxonomy_code": tax.get("code", ""),
                "taxonomy": tax.get("desc", ""),
                "status": r.get("basic", {}).get("status", ""),
                "street": loc.get("address_1", ""),
                "city": loc.get("city", ""),
                "state": loc.get("state", ""),
                "zip": str(loc.get("postal_code", ""))[:5],
            }
        )
    return rows


def nppes_fetch(url: str, postal_codes: list[str]) -> pd.DataFrame:
    rows: list[dict] = []
    for pc in postal_codes:
        skip = 0
        while True:
            params = {
                "version": "2.1",
                "enumeration_type": "NPI-2",
                "postal_code": pc,
                "limit": str(NPPES_PAGE),
                "skip": str(skip),
            }
            r = requests.get(url, params=params, timeout=120)
            r.raise_for_status()
            d = r.json()
            if d.get("Errors"):
                raise RuntimeError(f"NPPES {pc}: {d['Errors']}")
            res = d.get("results", [])
            rows += nppes_rows(res)
            if len(res) < NPPES_PAGE:
                break
            skip += NPPES_PAGE
            if skip > NPPES_MAX_SKIP:
                raise RuntimeError(f"NPPES {pc}: more than {NPPES_MAX_SKIP + NPPES_PAGE} results")
    return pd.DataFrame(rows).drop_duplicates("source_id").reset_index(drop=True)


def parse_geocoder(text: str) -> pd.DataFrame:
    """Census batch geocoder response → id, matched, lon, lat (unmatched rows: lon/lat NaN)."""
    out = []
    for row in csv.reader(io.StringIO(text)):
        if not row:
            continue
        matched = len(row) > 5 and row[2] == "Match"
        lon, lat = (float(v) for v in row[5].split(",")) if matched else (float("nan"),) * 2
        out.append({"id": row[0], "geocoded": matched, "lon": lon, "lat": lat})
    return pd.DataFrame(out, columns=["id", "geocoded", "lon", "lat"])


def geocode(url: str, df: pd.DataFrame) -> pd.DataFrame:
    buf = io.StringIO()
    df[["source_id", "street", "city", "state", "zip"]].to_csv(buf, index=False, header=False)
    r = requests.post(
        url,
        files={"addressFile": ("addresses.csv", buf.getvalue())},
        data={"benchmark": "Public_AR_Current"},
        timeout=600,
    )
    r.raise_for_status()
    return parse_geocoder(r.text)


def nppes(cfg: CityConfig, extent: BBox, force: bool = False) -> Path:
    out = cfg.raw_dir / "nppes_orgs.parquet"
    if out.exists() and not force:
        return out
    hc = cfg.scoring.xcheck["healthcare"]
    df = nppes_fetch(hc["nppes_url"], [str(p) for p in cfg.xcheck["nppes_postal_codes"]])
    geo = geocode(hc["geocoder_url"], df).rename(columns={"id": "source_id"})
    df = df.merge(geo, on="source_id", how="left")
    df["geocoded"] = df["geocoded"].fillna(False).astype(bool)
    print(f"[nppes] {len(df)} organizations, {df['geocoded'].sum()} geocoded")
    n_fetched, n_failed = len(df), int((~df["geocoded"]).sum())
    df = df[df["geocoded"]]
    g = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df["lon"], df["lat"]), crs=4326)
    g = _inside(g.drop(columns=["lon", "lat", "geocoded"]), extent)
    g["address"] = g["street"] + ", " + g["city"]
    g.to_parquet(out)
    write_meta(
        out,
        source="CMS NPPES NPI Registry (NPI-2 organizations), Census Geocoder",
        url=[hc["nppes_url"], hc["geocoder_url"]],
        postal_codes=cfg.xcheck["nppes_postal_codes"],
        fetched=n_fetched,
        not_geocoded=n_failed,
        rows=len(g),
        licence="public (CMS NPPES; Census Geocoder public domain)",
    )
    return out


def hrsa(cfg: CityConfig, extent: BBox, force: bool = False) -> Path:
    out = cfg.raw_dir / "hrsa_sites.parquet"
    if out.exists() and not force:
        return out
    url = cfg.scoring.xcheck["healthcare"]["hrsa_url"]
    r = requests.get(url, timeout=300)
    r.raise_for_status()
    d = pd.read_csv(io.BytesIO(r.content), dtype=str, encoding="latin1")
    x = pd.to_numeric(d["Geocoding Artifact Address Primary X Coordinate"], errors="coerce")
    y = pd.to_numeric(d["Geocoding Artifact Address Primary Y Coordinate"], errors="coerce")
    g = gpd.GeoDataFrame(
        {
            "source_id": d["BPHC Assigned Number"],
            "name": d["Site Name"],
            "site_type": d["Health Center Location Type Description"],
            "setting": d["Health Center Service Delivery Site Location Setting Description"],
            "status": d["Site Status Description"],
            "address": d["Site Address"].fillna("") + ", " + d["Site City"].fillna(""),
        },
        geometry=gpd.points_from_xy(x, y),
        crs=4326,
    )
    g = _inside(g[x.notna() & y.notna()], extent)
    g.to_parquet(out)
    write_meta(
        out,
        source="HRSA health center service delivery and look-alike sites",
        url=url,
        rows=len(g),
        licence="public domain (HRSA)",
    )
    print(f"[hrsa] {len(g)} sites")
    return out


def run(cfg: CityConfig, source: str, extent: BBox, force: bool = False) -> None:
    cfg.ensure_dirs()
    if source == "grocery":
        snap(cfg, extent, force)
    elif source == "healthcare":
        nppes(cfg, extent, force)
        hrsa(cfg, extent, force)
    elif source == "parks":
        city_parks(cfg, force)
