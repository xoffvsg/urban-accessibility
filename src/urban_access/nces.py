"""NCES school lists (public domain) → data/raw/<slug>/nces_schools.parquet (schools cross-check).

Public schools: EDGE ArcGIS REST services (CCD locations + administrative data with the grade span),
queried by the fetch extent. Private schools: the PSS public-use CSV (national, a few MB; cached).
PSS is a survey and non-respondents drop out of a year: older years fill the gaps (`year` column).
Grades are normalised to -1 = PK, 0 = K, 1..12; ungraded / not reported → missing.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests

from . import arcgis
from .census import write_meta
from .config import BBox, CityConfig

EDGE_URL = "https://nces.ed.gov/opengis/rest/services/K12_School_Locations"
PSS_URL = "https://nces.ed.gov/surveys/pss/zip/pss{year}_pu_csv.zip"
K8 = (0, 8)  # kindergarten .. 8th grade


def public_grade(code: object) -> int | None:
    """CCD GSLO/GSHI ("PK", "KG", "01".."12"; "UG", "M", "N" …) → grade number."""
    c = str(code).strip().upper()
    if c == "PK":
        return -1
    if c == "KG":
        return 0
    if c.isdigit() and 1 <= int(c) <= 12:
        return int(c)
    return None


def pss_grade(code: object) -> int | None:
    """PSS LOGR/HIGR codes: 1 ungraded, 2 PK, 3 K, 4 transitional K, 5 transitional 1st, 6..17.

    6..17 = 1st..12th grade (PSS user's manual, record layout).
    """
    try:
        c = int(float(str(code)))
    except ValueError:
        return None
    if c == 2:
        return -1
    if c in (3, 4):
        return 0
    if c == 5:
        return 1
    if 6 <= c <= 17:
        return c - 5
    return None


def serves_k8(lo: float | None, hi: float | None) -> bool:
    """Grade span overlaps K-8 (combined schools such as 1-12 count; PK-only and 9-12 don't)."""
    if lo is None or hi is None or pd.isna(lo) or pd.isna(hi):
        return False
    return lo <= K8[1] and hi >= K8[0]


def public_schools(extent: BBox, year: str) -> gpd.GeoDataFrame:
    loc = arcgis.query(
        f"{EDGE_URL}/EDGE_GEOCODE_PUBLICSCH_{year}/MapServer/0", extent=extent, geometry=False
    )
    if loc.empty:
        return _empty()
    ids = ",".join(f"'{i}'" for i in loc["NCESSCH"])
    adm = arcgis.query(
        f"{EDGE_URL}/EDGE_ADMINDATA_PUBLICSCH_{year}/MapServer/1",
        where=f"NCESSCH IN ({ids})",
        geometry=False,
    )
    df = loc.merge(adm, on="NCESSCH", how="left", suffixes=("", "_adm"))
    out = pd.DataFrame(
        {
            "source_id": df["NCESSCH"],
            "name": df["NAME"],
            "sector": "public",
            "grade_lo": df["GSLO"].map(public_grade),
            "grade_hi": df["GSHI"].map(public_grade),
            "status": df["SY_STATUS_TEXT"].fillna("unknown").str.lower(),
            "virtual": df["VIRTUAL"].fillna(""),
            "charter": df["CHARTER_TEXT"].fillna(""),
            "address": df["STREET"].fillna("") + ", " + df["CITY"].fillna(""),
            "year": year,
        }
    )
    return gpd.GeoDataFrame(out, geometry=gpd.points_from_xy(df["LON"], df["LAT"]), crs=4326)


def _pss_csv(year: str, cache: Path, force: bool) -> pd.DataFrame:
    if force or not cache.exists():
        r = requests.get(PSS_URL.format(year=year), timeout=300)
        r.raise_for_status()
        cache.write_bytes(r.content)
    with zipfile.ZipFile(cache) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        return pd.read_csv(io.BytesIO(z.read(name)), dtype=str, encoding="latin1")


def private_schools(extent: BBox, year: str, csv: pd.DataFrame) -> gpd.GeoDataFrame:
    yy, survey = year[-2:], "20" + year[-2:]  # "2324" → LATITUDE24, LOGR2024
    lat = pd.to_numeric(csv[f"LATITUDE{yy}"], errors="coerce")
    lon = pd.to_numeric(csv[f"LONGITUDE{yy}"], errors="coerce")
    inside = lat.between(extent.lat_min, extent.lat_max) & lon.between(
        extent.lon_min, extent.lon_max
    )
    df = csv[inside]
    out = pd.DataFrame(
        {
            "source_id": df["PPIN"],
            "name": df["PINST"],
            "sector": "private",
            "grade_lo": df[f"LOGR{survey}"].map(pss_grade),
            "grade_hi": df[f"HIGR{survey}"].map(pss_grade),
            "status": "open",  # listed in that year's survey
            "virtual": "",
            "charter": "",
            "address": df["PADDRS"].fillna("") + ", " + df["PCITY"].fillna(""),
            "year": year,
        }
    )
    return gpd.GeoDataFrame(out, geometry=gpd.points_from_xy(lon[inside], lat[inside]), crs=4326)


def _empty() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame({"source_id": []}, geometry=[], crs=4326)


def run(cfg: CityConfig, extent: BBox, force: bool = False) -> Path:
    cfg.ensure_dirs()
    out = cfg.raw_dir / "nces_schools.parquet"
    if out.exists() and not force:
        return out
    years = cfg.scoring.xcheck["schools"]
    pub_year, priv_years = str(years["public_year"]), [str(y) for y in years["private_years"]]
    parts = [public_schools(extent, pub_year)]
    seen: set[str] = set()
    for y in priv_years:  # newest first; older years only add schools missing from newer ones
        p = private_schools(extent, y, _pss_csv(y, cfg.raw_dir / f"pss{y}_pu_csv.zip", force))
        parts.append(p[~p["source_id"].isin(seen)])
        seen |= set(p["source_id"])
    schools = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs=4326)
    schools["serves_k8"] = [
        serves_k8(lo, hi) for lo, hi in zip(schools.grade_lo, schools.grade_hi, strict=False)
    ]
    schools.to_parquet(out)
    write_meta(
        out,
        source="NCES EDGE public school locations + administrative data (CCD); PSS private schools",
        url=[
            f"{EDGE_URL}/EDGE_GEOCODE_PUBLICSCH_{pub_year}",
            f"{EDGE_URL}/EDGE_ADMINDATA_PUBLICSCH_{pub_year}",
        ]
        + [PSS_URL.format(year=y) for y in priv_years],
        school_years={"public": pub_year, "private": priv_years},
        licence="public domain (U.S. Department of Education, NCES)",
        rows=len(schools),
    )
    print(f"[nces] {len(schools)} schools ({(schools.sector == 'public').sum()} public)")
    return out
