"""Optional GTFS feed → served stops as GeoParquet."""

from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests

from .census import write_meta
from .config import CityConfig


def download(url: str, dest: Path, force: bool = False) -> Path:
    if dest.exists() and not force:
        return dest
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    if not zipfile.is_zipfile(io.BytesIO(r.content)):
        raise RuntimeError(f"GTFS URL did not return a zip: {url}")
    dest.write_bytes(r.content)
    return dest


def _read(z: zipfile.ZipFile, name: str) -> pd.DataFrame:
    with z.open(name) as f:
        return pd.DataFrame(csv.DictReader(io.TextIOWrapper(f, "utf-8-sig")))


def served_stops(zip_path: Path) -> gpd.GeoDataFrame:
    """Boarding stops (location_type 0/empty) that appear in stop_times.txt, EPSG:4326."""
    with zipfile.ZipFile(zip_path) as z:
        stops = _read(z, "stops.txt")
        served = set(_read(z, "stop_times.txt")["stop_id"])
    lt = stops.get("location_type", pd.Series("", index=stops.index)).fillna("")
    stops = stops[lt.isin(["", "0"]) & stops["stop_id"].isin(served)]
    keep = [c for c in ("stop_id", "stop_code", "stop_name", "parent_station") if c in stops]
    return gpd.GeoDataFrame(
        stops[keep].reset_index(drop=True),
        geometry=gpd.points_from_xy(
            stops["stop_lon"].astype(float), stops["stop_lat"].astype(float)
        ),
        crs=4326,
    )


def run(cfg: CityConfig, force: bool = False) -> Path | None:
    if not cfg.gtfs_url:
        print("[gtfs] no transit.gtfs_url configured; transit falls back to Overture POIs")
        return None
    cfg.ensure_dirs()
    out = cfg.raw_dir / "gtfs_stops.parquet"
    if out.exists() and not force:
        return out
    z = download(cfg.gtfs_url, cfg.raw_dir / "gtfs.zip", force)
    stops = served_stops(z)
    stops.to_parquet(out)
    write_meta(out, source="GTFS stops.txt", url=cfg.gtfs_url, rows=len(stops))
    print(f"[gtfs] {len(stops)} served stops")
    return out
