"""Cross-checks of Overture places against authoritative open data → review tables.

`match` pairs places one-to-one by name and distance; the per-source review adds what the pipeline
currently counts and a suggestion. Nothing here changes the inputs: decisions go into the city's
include/exclude lists and place_overrides (PROJECT_NOTES §6a).
Statuses: confirmed (name match within confirm_m), moved (name match up to max_m), overture_only,
source_only.
"""

from __future__ import annotations

import functools
import re
import unicodedata
from difflib import SequenceMatcher

import geopandas as gpd
import pandas as pd

from . import nces, opendata
from .config import CityConfig
from .services import ASSIGNED, REF_LEN, apply_place_overrides, classify_places, normalise_name

CONFIRMED, MOVED, OVERTURE_ONLY, SOURCE_ONLY = "confirmed", "moved", "overture_only", "source_only"
# Words that say nothing about which place it is (level words like "middle" / "high" are kept).
GENERIC = {
    "school",
    "the",
    "of",
    "st",
    "saint",
    "and",
    "inc",
    "nv",
    "nevada",
    "co",
    "llc",
    "pc",
    "pllc",
    "ltd",
    "dba",
    "md",
}


def name_tokens(name: object) -> set[str]:
    if not isinstance(name, str):
        return set()
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"'s\b", "", s)
    out = set()
    for t in re.split(r"[^a-z0-9]+", s):
        if not t or t in GENERIC or t.isdigit():  # store numbers ("Walmart 3408") say nothing
            continue
        out.add(t[:-1] if len(t) > 3 and t.endswith("s") else t)  # "SMITHS" = "Smith's"
    return out


# Words too common to identify a place on their own (a one-word name sharing only one of these
# with a longer name is compared by characters instead).
WEAK = {
    "health",
    "medical",
    "center",
    "clinic",
    "care",
    "family",
    "group",
    "market",
    "food",
    "store",
    "pharmacy",
    "service",
    "physician",
    "community",
    "regional",
    "urgent",
}


def name_similarity(a: object, b: object) -> float:
    """Share of the shorter name's distinctive words found in the other (0–1).

    One-word names ("ISCHOOL" vs "I-School") fall back to a character ratio of the squashed names,
    and so does a one-word overlap on a weak word ("Health Co" vs "Sierra Family Health Center").
    """
    ta, tb = name_tokens(a), name_tokens(b)
    shared = ta & tb
    overlap = len(shared) / min(len(ta), len(tb)) if ta and tb else 0.0
    if min(len(ta), len(tb)) <= 1:
        if shared <= WEAK and ta != tb:
            overlap = 0.0
        overlap = max(overlap, SequenceMatcher(None, normalise_name(a), normalise_name(b)).ratio())
    return overlap


def match(
    overture: gpd.GeoDataFrame,
    source: gpd.GeoDataFrame,
    epsg: int,
    confirm_m: float,
    max_m: float,
    min_sim: float,
    prefer: set | None = None,
) -> pd.DataFrame:
    """Rows o_idx, s_idx (index labels or NA), dist_m, name_sim, status: pairs, then singletons.

    Greedy one-to-one: places in `prefer` (those the service counts) first, so a store isn't
    taken by a counter inside it ("Clinic at Walmart"); then best name match, shortest distance.
    """
    prefer = prefer or set()
    o = overture.to_crs(epsg)
    s = source.to_crs(epsg)
    pairs = []
    for si, sg in s.geometry.items():
        d = o.geometry.distance(sg)
        for oi in d.index[d <= max_m]:
            sim = name_similarity(o.at[oi, "name"], s.at[si, "name"])
            if sim >= min_sim:
                pairs.append((oi, si, float(d[oi]), sim))
    pairs.sort(key=lambda p: (p[0] not in prefer, -p[3], p[2]))
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
    review = _service_table(cfg)
    counted = set(
        review.loc[(review["service"] == "schools") & (review["status"] == ASSIGNED), "ref"]
    )
    src = gpd.read_parquet(cfg.raw_dir / "nces_schools.parquet")

    prefer = set(cand.index[cand["ref"].isin(counted)])
    m = match(
        cand,
        src,
        cfg.analysis_epsg,
        xc["confirm_m"],
        xc["max_m"],
        xc["min_name_similarity"],
        prefer,
    )
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
    return _sorted(out)


def _span(lo: object, hi: object) -> str:
    def g(v: object) -> str:
        if v is None or pd.isna(v):
            return "?"
        return {-1: "PK", 0: "K"}.get(int(v), str(int(v)))

    return "" if (lo is None or pd.isna(lo)) and (hi is None or pd.isna(hi)) else f"{g(lo)}-{g(hi)}"


@functools.cache
def _classified(slug: str) -> pd.DataFrame:
    from .config import load_city

    cfg = load_city(slug)
    places = gpd.read_parquet(cfg.raw_dir / "places.parquet")
    cls = classify_places(places, cfg.scoring, cfg.analysis_epsg)
    cls = cls.assign(lon=cls.geometry.x, lat=cls.geometry.y)
    return cls.drop(columns=["geometry", "name_norm"])


def _service_table(cfg: CityConfig) -> pd.DataFrame:
    """The current service classification (same rows as service_review.csv), from the config."""
    return _classified(cfg.slug).copy()


ORDER = {"drop": 0, "add": 1, "missing": 2, "review": 3, "ok": 4}


def _sorted(out: pd.DataFrame, name_col: str = "overture_name") -> pd.DataFrame:
    return out.sort_values(
        ["suggestion", "status", name_col],
        key=lambda c: c.map(ORDER) if c.name == "suggestion" else c,
    ).reset_index(drop=True)


def _candidates(cfg: CityConfig, terms: list[str] | None) -> gpd.GeoDataFrame:
    """Overture places (place_overrides applied: drops removed, moves and names corrected) whose
    taxonomy hierarchy has any of `terms`."""
    places = apply_place_overrides(
        gpd.read_parquet(cfg.raw_dir / "places.parquet"), cfg.scoring.place_overrides
    )
    places = places[~places["drop"].astype(bool)].drop(columns="drop")
    places["ref"] = places["id"].str[:REF_LEN]
    if terms:
        places = places[places["taxonomy_hierarchy"].map(lambda v: _hierarchy_has(v, set(terms)))]
    return places.copy()


def _counted(cfg: CityConfig, service: str) -> set[str]:
    review = _service_table(cfg)
    return set(review.loc[(review["service"] == service) & (review["status"] == ASSIGNED), "ref"])


def _paired(cand, src, m) -> tuple[pd.DataFrame, pd.DataFrame]:
    return (
        cand.reindex(m["o_idx"]).reset_index(drop=True),
        src.reindex(m["s_idx"]).reset_index(drop=True),
    )


def suggest_flag(status: str, in_service: bool, qualifies: bool) -> str:
    """Generic suggestion: does the source say the place belongs in the service?"""
    if status == MOVED:  # name match only, far away: never change the list on that alone
        return "review" if in_service or qualifies else "ok"
    if status == CONFIRMED:
        if qualifies and not in_service:
            return "add"
        if in_service and not qualifies:
            return "drop"
        return "ok"
    if status == OVERTURE_ONLY:
        return "review" if in_service else "ok"
    return "missing" if qualifies else "ok"


def grocery_review(cfg: CityConfig) -> pd.DataFrame:
    xc = cfg.scoring.xcheck
    cand = _candidates(cfg, None)  # any tag: full groceries hide as restaurants, butchers …
    counted = _counted(cfg, "grocery")
    src = gpd.read_parquet(cfg.raw_dir / "snap_retailers.parquet")
    src["full_service"] = src["store_type"].isin(xc["grocery"]["full_service"])
    prefer = set(cand.index[cand["ref"].isin(counted)])
    confirm_m = xc["grocery"].get("confirm_m", xc["confirm_m"])
    m = match(
        cand, src, cfg.analysis_epsg, confirm_m, xc["max_m"], xc["min_name_similarity"], prefer
    )
    o, s = _paired(cand, src, m)
    out = pd.DataFrame(
        {
            "status": m["status"],
            "ref": o["ref"],
            "overture_name": o["name"],
            "overture_category": o["taxonomy_primary"],
            "in_service": o["ref"].isin(counted),
            "source_id": s["source_id"],
            "source_name": s["name"],
            "store_type": s["store_type"],
            "full_service": s["full_service"].fillna(False).astype(bool),
            "dist_m": m["dist_m"],
            "name_sim": m["name_sim"],
            "address": s["address"],
        }
    )
    # unmatched Overture places matter only when counted; unmatched SNAP stores when full-service
    keep = (out["status"] != OVERTURE_ONLY) | out["in_service"]
    keep &= (out["status"] != SOURCE_ONLY) | out["full_service"]
    out = out[keep].reset_index(drop=True)
    sug = [
        suggest_flag(*r)
        for r in zip(out["status"], out["in_service"], out["full_service"], strict=True)
    ]
    out.insert(1, "suggestion", sug)
    return _sorted(out)


def is_essential(code: object, prefixes: list[str]) -> bool:
    return isinstance(code, str) and any(code.startswith(p) for p in prefixes)


def health_sources(
    nppes_orgs: gpd.GeoDataFrame, hrsa: gpd.GeoDataFrame, prefixes: list[str]
) -> gpd.GeoDataFrame:
    """One row per (source record, name): NPPES legal + other names, HRSA sites."""
    n = nppes_orgs.assign(
        names=(nppes_orgs["name"] + "; " + nppes_orgs["other_names"].fillna("")).str.split("; ")
    ).explode("names")
    n = n[n["names"].str.strip() != ""]
    n = n.assign(
        name=n["names"].str.strip(),
        source="nppes",
        essential=n["taxonomy_code"].map(lambda c: is_essential(c, prefixes)),
    ).drop(columns="names")
    h = hrsa.assign(
        source="hrsa",
        taxonomy_code="HRSA",
        taxonomy="health center: "
        + hrsa["site_type"].fillna("")
        + " / "
        + hrsa["setting"].fillna(""),
        essential=hrsa["status"].eq("Active")
        & hrsa["site_type"].eq("Permanent")
        & ~hrsa["setting"].fillna("").str.contains("School", case=False),
    )
    cols = [
        "source_id",
        "name",
        "source",
        "taxonomy_code",
        "taxonomy",
        "essential",
        "address",
        "geometry",
    ]
    both = pd.concat([n[cols], h[cols]], ignore_index=True)
    return gpd.GeoDataFrame(both, geometry="geometry", crs=4326)


def healthcare_review(cfg: CityConfig) -> pd.DataFrame:
    """Per Overture health place: the NPPES/HRSA records with a matching name at its site.

    Not one-to-one: one organization (NPI) can run several sites and one site hosts several NPIs,
    so a place qualifies when any name-matched record within confirm_m is essential care.
    """
    xc = cfg.scoring.xcheck
    hc = xc["healthcare"]
    epsg = cfg.analysis_epsg
    counted = _counted(cfg, "healthcare")
    allp = _candidates(cfg, None)
    terms = set(hc["overture_taxonomy"])
    cand = allp[
        allp["ref"].isin(counted)
        | allp["taxonomy_hierarchy"].map(lambda v: _hierarchy_has(v, terms))
    ]
    src = health_sources(
        gpd.read_parquet(cfg.raw_dir / "nppes_orgs.parquet"),
        gpd.read_parquet(cfg.raw_dir / "hrsa_sites.parquet"),
        hc["essential_taxonomy"],
    ).to_crs(epsg)
    min_sim = hc.get("min_name_similarity", xc["min_name_similarity"])
    rows, used = [], set()
    for _, p in cand.to_crs(epsg).iterrows():
        near = src[src.distance(p.geometry) <= xc["max_m"]].copy()
        near["dist_m"] = near.distance(p.geometry)
        near["name_sim"] = near["name"].map(lambda n, pn=p["name"]: name_similarity(pn, n))
        hit = near[near["name_sim"] >= min_sim].sort_values(
            ["essential", "dist_m"], ascending=[False, True]
        )
        site = hit[hit["dist_m"] <= xc["confirm_m"]]
        best = site.iloc[0] if len(site) else (hit.iloc[0] if len(hit) else None)
        status = CONFIRMED if len(site) else (MOVED if len(hit) else OVERTURE_ONLY)
        used |= set(hit.index)
        rows.append(
            {
                "status": status,
                "ref": p["ref"],
                "overture_name": p["name"],
                "overture_category": p["taxonomy_primary"],
                "in_service": p["ref"] in counted,
                "source": None if best is None else best["source"],
                "source_id": None if best is None else best["source_id"],
                "source_name": None if best is None else best["name"],
                "taxonomy": None if best is None else best["taxonomy"],
                "essential": bool(len(site) and site["essential"].any())
                if status == CONFIRMED
                else bool(best is not None and best["essential"]),
                "dist_m": None if best is None else round(float(best["dist_m"]), 1),
                "name_sim": None if best is None else round(float(best["name_sim"]), 2),
                "matched_taxonomies": "; ".join(sorted(set(site["taxonomy"].dropna())))[:300],
                "address": None if best is None else best["address"],
            }
        )
    out = pd.DataFrame(rows)
    out = out[(out["status"] != OVERTURE_ONLY) | out["in_service"]]
    # essential records that no Overture health place matched: possibly missing from Overture
    miss = src[~src.index.isin(used) & src["essential"]].drop_duplicates("source_id")
    out = pd.concat(
        [
            out,
            pd.DataFrame(
                {
                    "status": SOURCE_ONLY,
                    "in_service": False,
                    "source": miss["source"],
                    "source_id": miss["source_id"],
                    "source_name": miss["name"],
                    "taxonomy": miss["taxonomy"],
                    "essential": True,
                    "address": miss["address"],
                }
            ),
        ],
        ignore_index=True,
    )
    sug = [
        suggest_flag(*r)
        for r in zip(out["status"], out["in_service"], out["essential"], strict=True)
    ]
    out.insert(1, "suggestion", sug)
    out["note"] = ""
    # a second Overture point for a site that is already counted adds nothing
    counted_sites = set(out.loc[out["in_service"] & out["essential"], "source_id"].dropna())
    dup = (out["suggestion"] == "add") & out["source_id"].isin(counted_sites)
    out.loc[dup, ["suggestion", "note"]] = ["ok", "same site as a counted place"]
    return _sorted(out)


def city_park_additions(
    city: gpd.GeoDataFrame,
    overture_parks: gpd.GeoDataFrame,
    categories: list[str],
    exclude: list[str],
    epsg: int,
) -> pd.Series:
    """Boolean per city polygon: an eligible park that overlaps no Overture park polygon."""
    c = city.to_crs(epsg)
    o = overture_parks.to_crs(epsg)
    hit = c.geometry.map(lambda g: bool(o.intersects(g).any()))
    pat = "|".join(re.escape(x) for x in exclude) if exclude else None
    excluded = c["name"].fillna("").str.contains(pat, case=False) if pat else False
    return c["category"].isin(categories) & ~hit & ~excluded


def parks_review(cfg: CityConfig) -> pd.DataFrame:
    xp = cfg.scoring.xcheck["parks"]
    epsg = cfg.analysis_epsg
    city = gpd.read_parquet(cfg.raw_dir / "city_parks.parquet")
    ov = gpd.read_parquet(cfg.raw_dir / "parks.parquet")
    review = _service_table(cfg)
    poi = review[(review["service"] == "parks") & (review["status"] == ASSIGNED)]
    poi = gpd.GeoDataFrame(
        poi, geometry=gpd.points_from_xy(poi["lon"], poi["lat"]), crs=4326
    ).to_crs(epsg)
    c, o = city.to_crs(epsg), ov.to_crs(epsg)
    add = city_park_additions(city, ov, xp["categories"], xp["exclude_names"], epsg)
    rows = []
    for i, r in c.iterrows():
        polys = o[o.intersects(r.geometry)]
        rows.append(
            {
                "status": CONFIRMED if len(polys) else SOURCE_ONLY,
                "suggestion": "add" if add[i] and not cfg.extra_parks else "ok",
                "note": "added to the parks layer (extra_parks)"
                if add[i] and cfg.extra_parks
                else "",
                "source_id": r["source_id"],
                "source_name": r["name"],
                "category": r["category"],
                "area_ha": round(r.geometry.area / 1e4, 2),
                "overture_polygons": len(polys),
                "overture_names": "; ".join(sorted(set(polys["name"].dropna()))),
                "overture_pois_50m": int((poi.distance(r.geometry) <= 50).sum()),
            }
        )
    out = pd.DataFrame(rows)
    # Overture park polygons inside the city's coverage (Carson City) that no city polygon touches
    bg = gpd.read_parquet(cfg.raw_dir / "block_groups.parquet").to_crs(epsg)
    county = cfg.xcheck.get("city_parks_county_geoid", "32510")
    in_county = bg["geoid"].astype(str).str.startswith(str(county))
    if in_county.any():
        area = bg[in_county].union_all()
        lone = o[o.within(area) & ~o.intersects(c.union_all())]
        out = pd.concat(
            [
                out,
                pd.DataFrame(
                    {
                        "status": OVERTURE_ONLY,
                        "suggestion": "review",
                        "overture_names": lone["name"].fillna("(unnamed " + lone["class"] + ")"),
                        "category": "overture:" + lone["class"],
                        "area_ha": (lone.area / 1e4).round(2),
                        "overture_polygons": 1,
                    }
                ),
            ],
            ignore_index=True,
        )
    return _sorted(out, "source_name")


HOMES_SQL = """
WITH heur AS (
    SELECT h.id, h.bg_geoid, g.pop_total * h.weight / sum(h.weight) OVER (PARTITION BY h.bg_geoid)
           AS pop
    FROM res_heuristic h JOIN block_groups g ON g.geoid = h.bg_geoid
), now AS (
    SELECT o.ref AS id, o.bg_geoid, o.pop FROM origins o WHERE o.kind = 'building'
), moved AS (
    SELECT coalesce(n.bg_geoid, h.bg_geoid) AS bg_geoid,
           abs(coalesce(n.pop, 0) - coalesce(h.pop, 0)) AS d
    FROM now n FULL JOIN heur h ON h.id = n.id
)
SELECT g.geoid, left(g.geoid, 5) AS county, g.pop_total,
       (SELECT count(*) FROM res_heuristic h WHERE h.bg_geoid = g.geoid) AS heuristic_homes,
       (SELECT count(*) FROM res_buildings r WHERE r.bg_geoid = g.geoid
            AND r.kind IN ('parcel_building', 'res_class', 'unclassified')) AS building_homes,
       (SELECT count(*) FROM res_buildings r WHERE r.bg_geoid = g.geoid
            AND r.kind = 'parcel_point') AS parcel_points,
       (SELECT sum(r.weight) FROM res_buildings r WHERE r.bg_geoid = g.geoid
            AND r.kind LIKE 'parcel%%') AS parcel_units,
       (SELECT sum(d) / 2 FROM moved m WHERE m.bg_geoid = g.geoid) AS pop_moved
FROM block_groups g WHERE g.pop_total > 0 ORDER BY g.geoid
"""


def homes_review(cfg: CityConfig) -> pd.DataFrame:
    """Per populated block group: footprint heuristic vs parcel dwelling units vs ACS households.

    Reads the tables built by `make network` (sql/30_origins.sql), so run after a build.
    """
    from sqlalchemy import text

    from . import db

    with db.engine().connect() as con:
        con.exec_driver_sql(f'SET search_path = "{cfg.slug}", public')
        out = pd.read_sql(text(HOMES_SQL), con)
    acs = pd.read_parquet(cfg.raw_dir / "acs.parquet")
    acs = acs.reset_index() if "geoid" not in acs else acs
    out = out.merge(acs[["geoid", "hh_total", "hh_total_moe"]], on="geoid", how="left")
    out["units_per_household"] = (out["parcel_units"] / out["hh_total"]).round(2)
    out["pop_moved_pct"] = (100 * out["pop_moved"] / out["pop_total"]).round(1)
    out["flag"] = ""
    lo, hi = cfg.scoring.xcheck.get("homes_units_per_hh", [0.8, 1.5])
    off = out["units_per_household"].notna() & ~out["units_per_household"].between(lo, hi)
    out.loc[off, "flag"] = f"units/households outside {lo}-{hi}"
    pph = out["pop_total"] / out["hh_total"]
    out.loc[pph > 4, "flag"] = (
        out.loc[pph > 4, "flag"] + "; residents/household > 4 (group quarters?)"
    ).str.lstrip("; ")
    out.insert(1, "suggestion", ["review" if f else "ok" for f in out["flag"]])
    _homes_diff_layer(cfg)
    return out.round({"parcel_units": 0, "pop_moved": 0})


def _homes_diff_layer(cfg: CityConfig) -> None:
    """Optional QGIS layer (data/interim/<slug>/homes_diff.gpkg, not committed): buildings that are
    homes under only one method, and parcel points (dwelling units with no Overture building)."""
    from . import db

    q = """
        SELECT h.id, 'heuristic_only' AS diff, h.kind, h.geom FROM res_heuristic h
        WHERE NOT EXISTS (SELECT 1 FROM res_buildings r WHERE r.id = h.id)
        UNION ALL
        SELECT r.id, 'parcel_only', r.kind, r.geom FROM res_buildings r
        WHERE r.kind = 'parcel_building'
          AND NOT EXISTS (SELECT 1 FROM res_heuristic h WHERE h.id = r.id)
        UNION ALL
        SELECT r.parcel_id, 'parcel_point', r.kind, r.geom FROM res_buildings r
        WHERE r.kind = 'parcel_point'
    """
    with db.engine().connect() as con:
        con.exec_driver_sql(f'SET search_path = "{cfg.slug}", public')
        g = gpd.read_postgis(q, con, geom_col="geom")
    g.to_file(cfg.interim_dir / "homes_diff.gpkg", driver="GPKG")


REVIEWS = {
    "schools": (nces.run, schools_review),
    "grocery": (None, grocery_review),
    "healthcare": (None, healthcare_review),
    "parks": (None, parks_review),
    "homes": (None, homes_review),
}


def run(cfg: CityConfig, source: str, extent, force: bool = False) -> dict[str, pd.DataFrame]:
    sources = list(REVIEWS) if source == "all" else [source]
    out = {}
    for src in sources:
        if src not in REVIEWS:
            raise ValueError(f"unknown cross-check source: {src}")
        fetch, review = REVIEWS[src]
        if fetch is not None:
            fetch(cfg, extent, force=force)
        else:
            opendata.run(cfg, src, extent, force=force)
        df = review(cfg)
        path = cfg.interim_dir / f"xcheck_{src}.csv"
        df.to_csv(path, index=False)
        counts = ", ".join(f"{k} {v}" for k, v in df["suggestion"].value_counts().items())
        print(f"[xcheck] {path.name}: {counts}")
        out[src] = df
    return out
