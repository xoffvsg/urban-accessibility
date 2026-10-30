"""TIGER/Line block groups (pygris) and ACS 5-year block-group estimates (Census API)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pygris
import requests
from shapely.geometry import box

from .config import BBox, CityConfig, settings

ACS_URL = "https://api.census.gov/data/{year}/acs/acs5"
MAX_VARS_PER_CALL = 45  # API limit is 50 incl. geography fields

# B01001 sex-by-age line numbers
_UNDER18 = [3, 4, 5, 6, 27, 28, 29, 30]
_AGE65 = [20, 21, 22, 23, 24, 25, 44, 45, 46, 47, 48, 49]

ACS_VARS: dict[str, str] = {
    "B01003_001": "pop_total",
    "B19013_001": "median_hh_income",
    "B25044_001": "hh_total",
    "B25044_003": "hh_owner_no_vehicle",
    "B25044_010": "hh_renter_no_vehicle",
    **{f"B01001_{n:03d}": f"b01001_{n:03d}" for n in _UNDER18 + _AGE65},
}

# Census "annotation" sentinels (median not computable, top-coded, etc.)
_SENTINELS = [-111111111, -222222222, -333333333, -555555555, -666666666, -888888888, -999999999]


def detect_counties(cfg: CityConfig) -> list[tuple[str, str]]:
    """(state_fips, county_fips) pairs intersecting the city bbox, or the configured values."""
    st, co = cfg.state_fips, cfg.county_fips
    if st != "auto" and co != "auto":
        sts = [st] if isinstance(st, str | int) else list(st)
        cos = [co] if isinstance(co, str | int) else list(co)
        if len(sts) == 1 and len(cos) > 1:
            sts = sts * len(cos)
        if len(sts) != len(cos):
            raise ValueError("state_fips and county_fips lists must have the same length")
        return [(f"{int(s):02d}", f"{int(c):03d}") for s, c in zip(sts, cos, strict=True)]

    counties = pygris.counties(cb=True, year=cfg.acs_year, cache=True)
    hit = counties[counties.to_crs(4326).intersects(box(*cfg.bbox.as_tuple()))]
    if hit.empty:
        raise ValueError(f"no counties intersect bbox {cfg.bbox}")
    return sorted(zip(hit["STATEFP"], hit["COUNTYFP"], strict=True))


def block_groups(cfg: CityConfig, counties: list[tuple[str, str]]) -> gpd.GeoDataFrame:
    """TIGER block groups for the counties (EPSG:4326) with `tract_geoid` and `scored` flag.

    `scored` = centroid (computed in the analysis CRS) lies inside the city bbox.
    """
    parts = [
        pygris.block_groups(state=s, county=c, year=cfg.acs_year, cb=False, cache=True)
        for s, c in counties
    ]
    bg = pd.concat(parts, ignore_index=True)
    bg = gpd.GeoDataFrame(bg, geometry="geometry", crs=parts[0].crs)
    bg = bg.rename(columns=str.lower)
    bg = bg[["geoid", "statefp", "countyfp", "tractce", "blkgrpce", "aland", "awater", "geometry"]]
    bg["tract_geoid"] = bg["geoid"].str[:11]
    cent = bg.to_crs(cfg.analysis_epsg).centroid.to_crs(4326)
    bg = bg.to_crs(4326)
    bg["scored"] = cent.within(box(*cfg.bbox.as_tuple())).to_numpy()
    bg["tiger_year"] = cfg.acs_year
    return bg


def _acs_request(
    year: int, variables: list[str], state: str, county: str, key: str
) -> pd.DataFrame:
    params = {
        "get": ",".join(variables),
        "for": "block group:*",
        "in": f"state:{state} county:{county} tract:*",
        "key": key,
    }
    r = requests.get(ACS_URL.format(year=year), params=params, timeout=120)
    if r.status_code != 200:
        # never echo the URL: it contains the API key
        raise RuntimeError(f"Census API {r.status_code}: {r.text[:300]}")
    rows = r.json()
    return pd.DataFrame(rows[1:], columns=rows[0])


def _moe_sum(moes: pd.DataFrame) -> pd.Series:
    """ACS approximation for the MOE of a sum: sqrt(Σ MOE²)."""
    return np.sqrt((moes**2).sum(axis=1, min_count=1))


def acs(cfg: CityConfig, counties: list[tuple[str, str]]) -> pd.DataFrame:
    """ACS 5-year estimates + MOEs per block group, with derived equity fields."""
    key = settings().census_api_key
    if not key:
        raise RuntimeError("CENSUS_API_KEY missing from .env")
    codes = [c + s for c in ACS_VARS for s in ("E", "M")]
    frames = []
    for state, county in counties:
        chunks = []
        for i in range(0, len(codes), MAX_VARS_PER_CALL):
            df = _acs_request(cfg.acs_year, codes[i : i + MAX_VARS_PER_CALL], state, county, key)
            df["geoid"] = df["state"] + df["county"] + df["tract"] + df["block group"]
            chunks.append(
                df.set_index("geoid").drop(columns=["state", "county", "tract", "block group"])
            )
        frames.append(pd.concat(chunks, axis=1))
    raw = pd.concat(frames).apply(pd.to_numeric, errors="coerce")
    raw = raw.mask(raw.isin(_SENTINELS))

    out = pd.DataFrame(index=raw.index)
    for code, name in ACS_VARS.items():
        out[name] = raw[code + "E"]
        out[name + "_moe"] = raw[code + "M"]

    under = [f"b01001_{n:03d}" for n in _UNDER18]
    old = [f"b01001_{n:03d}" for n in _AGE65]
    nocar = ["hh_owner_no_vehicle", "hh_renter_no_vehicle"]
    out["pop_under18"] = out[under].sum(axis=1, min_count=1)
    out["pop_under18_moe"] = _moe_sum(out[[c + "_moe" for c in under]])
    out["pop_65plus"] = out[old].sum(axis=1, min_count=1)
    out["pop_65plus_moe"] = _moe_sum(out[[c + "_moe" for c in old]])
    out["hh_no_vehicle"] = out[nocar].sum(axis=1, min_count=1)
    out["hh_no_vehicle_moe"] = _moe_sum(out[[c + "_moe" for c in nocar]])
    out = out.drop(columns=[c for c in out.columns if c.startswith("b01001_")])

    out["pop_under18_pct"] = 100 * out["pop_under18"] / out["pop_total"].replace(0, np.nan)
    out["pop_65plus_pct"] = 100 * out["pop_65plus"] / out["pop_total"].replace(0, np.nan)
    out["zero_car_hh_pct"] = 100 * out["hh_no_vehicle"] / out["hh_total"].replace(0, np.nan)
    # MOE of a proportion (ACS handbook); falls back to the ratio formula when the radicand < 0
    p = out["zero_car_hh_pct"] / 100
    num, den = out["hh_no_vehicle_moe"], out["hh_total_moe"]
    rad = num**2 - p**2 * den**2
    rad = rad.where(rad >= 0, num**2 + p**2 * den**2)
    out["zero_car_hh_pct_moe"] = 100 * np.sqrt(rad) / out["hh_total"].replace(0, np.nan)
    # coefficient of variation (%) for the headline estimates: > 30 % = unreliable
    for c in ("pop_total", "median_hh_income", "zero_car_hh_pct"):
        out[c + "_cv"] = 100 * (out[c + "_moe"] / 1.645) / out[c].replace(0, np.nan)
    out["acs_year"] = cfg.acs_year
    return out.reset_index()


def write_meta(path: Path, **meta: object) -> None:
    meta["created_utc"] = datetime.now(UTC).isoformat(timespec="seconds")
    path.with_suffix(".meta.json").write_text(json.dumps(meta, indent=2, default=str))


def run(cfg: CityConfig, force: bool = False) -> dict[str, Path]:
    """Write block_groups.parquet and acs.parquet; return their paths."""
    cfg.ensure_dirs()
    bg_path, acs_path = cfg.raw_dir / "block_groups.parquet", cfg.raw_dir / "acs.parquet"
    counties = None
    if force or not bg_path.exists():
        counties = detect_counties(cfg)
        print(f"[census] counties {counties}; TIGER {cfg.acs_year} block groups …", flush=True)
        bg = block_groups(cfg, counties)
        bg.to_parquet(bg_path)
        write_meta(
            bg_path,
            source="TIGER/Line via pygris",
            year=cfg.acs_year,
            counties=counties,
            rows=len(bg),
            scored=int(bg["scored"].sum()),
        )
    if force or not acs_path.exists():
        counties = counties or detect_counties(cfg)
        print(f"[census] ACS 5-year {cfg.acs_year} …", flush=True)
        df = acs(cfg, counties)
        df.to_parquet(acs_path, index=False)
        write_meta(
            acs_path,
            source="ACS 5-year API",
            year=cfg.acs_year,
            counties=counties,
            variables=list(ACS_VARS),
            rows=len(df),
        )
    return {"block_groups": bg_path, "acs": acs_path}


def scored_extent(cfg: CityConfig) -> BBox:
    """City bbox ∪ envelope of scored block groups (requires block_groups.parquet)."""
    bg = gpd.read_parquet(cfg.raw_dir / "block_groups.parquet")
    xmin, ymin, xmax, ymax = bg[bg["scored"]].total_bounds
    return cfg.bbox.union(BBox(xmin, ymin, xmax, ymax))
