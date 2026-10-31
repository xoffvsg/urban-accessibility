"""Cross-checks of Overture places against authoritative open data → review tables.

`match` pairs places one-to-one by name and distance; the per-source review adds what the pipeline
currently counts and a suggestion. Nothing here changes the inputs: decisions go into the city's
include/exclude lists and place_overrides (PROJECT_NOTES §6a).
Statuses: confirmed (name match within confirm_m), moved (name match up to max_m), overture_only,
source_only.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

import geopandas as gpd
import pandas as pd

from . import nces
from .config import CityConfig
from .services import ASSIGNED, REF_LEN, normalise_name

CONFIRMED, MOVED, OVERTURE_ONLY, SOURCE_ONLY = "confirmed", "moved", "overture_only", "source_only"
# Words that say nothing about which place it is (level words like "middle" / "high" are kept).
GENERIC = {"school", "the", "of", "st", "saint", "and", "inc", "nv", "nevada", "co"}


def name_tokens(name: object) -> set[str]:
    if not isinstance(name, str):
        return set()
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"'s\b", "", s)
    return {t for t in re.split(r"[^a-z0-9]+", s) if t and t not in GENERIC}


def name_similarity(a: object, b: object) -> float:
    """Share of the shorter name's distinctive words found in the other (0–1).

    One-word names ("ISCHOOL" vs "I-School") fall back to a character ratio of the squashed names.
    """
    ta, tb = name_tokens(a), name_tokens(b)
    overlap = len(ta & tb) / min(len(ta), len(tb)) if ta and tb else 0.0
    if min(len(ta), len(tb)) <= 1:
        overlap = max(overlap, SequenceMatcher(None, normalise_name(a), normalise_name(b)).ratio())
    return overlap


def match(
    overture: gpd.GeoDataFrame,
    source: gpd.GeoDataFrame,
    epsg: int,
    confirm_m: float,
    max_m: float,
    min_sim: float,
) -> pd.DataFrame:
    """Rows o_idx, s_idx (index labels or NA), dist_m, name_sim, status: pairs, then singletons.

    Greedy one-to-one: best name similarity first, then shortest distance.
    """
    o = overture.to_crs(epsg)
    s = source.to_crs(epsg)
    pairs = []
    for si, sg in s.geometry.items():
        d = o.geometry.distance(sg)
        for oi in d.index[d <= max_m]:
            sim = name_similarity(o.at[oi, "name"], s.at[si, "name"])
            if sim >= min_sim:
                pairs.append((oi, si, float(d[oi]), sim))
    pairs.sort(key=lambda p: (-p[3], p[2]))
    used_o, used_s, rows = set(), set(), []
    for oi, si, dist, sim in pairs:
        if oi in used_o or si in used_s:
            continue
        used_o.add(oi)
        used_s.add(si)
        rows.append(
            (oi, si, round(dist, 1), round(sim, 2), CONFIRMED if dist <= confirm_m else MOVED)
        )
    rows += [(oi, pd.NA, None, None, OVERTURE_ONLY) for oi in o.index if oi not in used_o]
    rows += [(pd.NA, si, None, None, SOURCE_ONLY) for si in s.index if si not in used_s]
    return pd.DataFrame(rows, columns=["o_idx", "s_idx", "dist_m", "name_sim", "status"])


def _hierarchy_has(v: object, terms: set[str]) -> bool:
    return v is not None and not isinstance(v, float) and bool(terms & set(v))


def suggest_school(status: str, in_service: bool, serves: bool | None, open_: bool) -> str:
    """ok / add / drop / review for one row of the schools review."""
    if status in (CONFIRMED, MOVED):
        k8 = bool(serves) and open_
        if k8 and not in_service:
            return "add"
        if in_service and not k8:
            return "drop"
        return "review" if status == MOVED and in_service else "ok"
    if status == OVERTURE_ONLY:
        return "review" if in_service else "ok"
    return "missing" if serves and open_ else "ok"  # source_only: not in Overture at all


def schools_review(cfg: CityConfig) -> pd.DataFrame:
    xc = cfg.scoring.xcheck
    places = gpd.read_parquet(cfg.raw_dir / "places.parquet")
    terms = set(xc["overture_taxonomy"])
    cand = places[places["taxonomy_hierarchy"].map(lambda v: _hierarchy_has(v, terms))].copy()
    cand["ref"] = cand["id"].str[:REF_LEN]
    review = pd.read_csv(cfg.interim_dir / "service_review.csv")
    counted = set(
        review.loc[(review["service"] == "schools") & (review["status"] == ASSIGNED), "ref"]
    )
    src = gpd.read_parquet(cfg.raw_dir / "nces_schools.parquet")

    m = match(cand, src, cfg.analysis_epsg, xc["confirm_m"], xc["max_m"], xc["min_name_similarity"])
    o = cand.reindex(m["o_idx"]).reset_index(drop=True)
    s = src.reindex(m["s_idx"]).reset_index(drop=True)
    out = pd.DataFrame(
        {
            "status": m["status"],
            "ref": o["ref"],
            "overture_name": o["name"],
            "overture_category": o["taxonomy_primary"],
            "in_service": o["ref"].isin(counted),
            "source_id": s["source_id"],
            "source_name": s["name"],
            "sector": s["sector"],
            "grades": [_span(lo, hi) for lo, hi in zip(s["grade_lo"], s["grade_hi"], strict=False)],
            "serves_k8": s["serves_k8"],
            "source_status": s["status"],
            "virtual": s["virtual"],
            "year": s["year"],
            "dist_m": m["dist_m"],
            "name_sim": m["name_sim"],
            "address": s["address"],
        }
    )
    # Overture-only rows: keep counted places and school-like ones; drop tutors, beauty academies …
    school_like = o["taxonomy_hierarchy"].map(lambda v: _hierarchy_has(v, {"school"}))
    keep = (out["status"] != OVERTURE_ONLY) | out["in_service"] | school_like
    out = out[keep].reset_index(drop=True)
    open_ = out["source_status"].eq("open") & ~out["virtual"].fillna("").str.startswith("Full")
    out.insert(
        1,
        "suggestion",
        [
            suggest_school(st, inn, sv if pd.notna(sv) else None, op)
            for st, inn, sv, op in zip(
                out["status"], out["in_service"], out["serves_k8"], open_, strict=False
            )
        ],
    )
    older = (
        out["year"].notna()
        & (out["sector"] == "private")
        & (out["year"] != str(xc["schools"]["private_years"][0]))
    )
    out["note"] = ""
    out.loc[older, "note"] = "only in an older PSS year (non-response or closed)"
    order = {"drop": 0, "add": 1, "missing": 2, "review": 3, "ok": 4}
    return out.sort_values(
        ["suggestion", "status", "overture_name"],
        key=lambda c: c.map(order) if c.name == "suggestion" else c,
    ).reset_index(drop=True)


def _span(lo: object, hi: object) -> str:
    def g(v: object) -> str:
        if v is None or pd.isna(v):
            return "?"
        return {-1: "PK", 0: "K"}.get(int(v), str(int(v)))

    return "" if (lo is None or pd.isna(lo)) and (hi is None or pd.isna(hi)) else f"{g(lo)}-{g(hi)}"


def run(cfg: CityConfig, source: str, extent, force: bool = False) -> pd.DataFrame:
    if source != "schools":
        raise ValueError(f"unknown cross-check source: {source}")
    nces.run(cfg, extent, force=force)
    out = schools_review(cfg)
    path = cfg.interim_dir / "xcheck_schools.csv"
    out.to_csv(path, index=False)
    print(
        f"[xcheck] {path}: "
        + ", ".join(f"{k} {v}" for k, v in out["suggestion"].value_counts().items())
    )
    return out
