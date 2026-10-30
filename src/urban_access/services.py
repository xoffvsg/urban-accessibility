"""Classify Overture places into service types using scoring.yaml + city overrides."""

from __future__ import annotations

import re
from collections.abc import Iterable

import geopandas as gpd
import numpy as np
import pandas as pd

from .config import Scoring, ServiceRule

CLOSED = "permanently_closed"

# status values in the review table, in order of precedence
ASSIGNED = "assigned"
ST_CLOSED = "closed"
ST_LOW_CONF = "low_confidence"
ST_EXCLUDED = "excluded_name"
ST_DUPLICATE = "duplicate"


def _as_list(v: object) -> list[str]:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return []
    if isinstance(v, str):
        return [v]
    return [str(x) for x in v]  # list / ndarray from parquet


def _first_match(patterns: Iterable[str], text: str) -> str | None:
    for p in patterns:
        if re.search(p, text, flags=re.IGNORECASE):
            return p
    return None


def _taxonomy_hit(row: pd.Series, values: Iterable[str]) -> str | None:
    terms = set(_as_list(row.get("taxonomy_hierarchy"))) | set(
        _as_list(row.get("taxonomy_alternates"))
    )
    primary = row.get("taxonomy_primary")
    if isinstance(primary, str):
        terms.add(primary)
    for v in values:
        if v in terms:
            return v
    return None


def normalise_name(name: object) -> str:
    if not isinstance(name, str):
        return ""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def match_rule(
    row: pd.Series, rule: ServiceRule, taxonomy: Iterable[str]
) -> tuple[str, str] | None:
    """Return (rule_hit, status) when the place is a candidate for this rule, else None."""
    name = row.get("name") if isinstance(row.get("name"), str) else ""
    tax = _taxonomy_hit(row, taxonomy)
    inc = _first_match(rule.include_names, name) if name else None
    if tax is None and inc is None:
        return None
    hit = f"taxonomy:{tax}" if tax else f"name:{inc}"
    if row.get("operating_status") == CLOSED:
        return hit, ST_CLOSED
    exc = _first_match(rule.exclude_names, name) if name else None
    if exc is not None:
        return f"{hit} | exclude:{exc}", ST_EXCLUDED
    conf = row.get("confidence")
    if conf is None or pd.isna(conf) or float(conf) < rule.min_confidence:
        return hit, ST_LOW_CONF
    return hit, ASSIGNED


def _dedupe(cands: gpd.GeoDataFrame, metres: float, epsg: int) -> pd.Series:
    """Mark later rows with the same normalised name within `metres` as duplicates.

    Rows must already be sorted best-first (highest confidence); returns a boolean mask.
    """
    dup = pd.Series(False, index=cands.index)
    if cands.empty:
        return dup
    pts = cands.geometry.to_crs(epsg).representative_point()
    for _, grp in cands.groupby(["service", "name_norm"], sort=False):
        if len(grp) < 2 or grp["name_norm"].iat[0] == "":
            continue
        kept: list[int] = []
        for idx in grp.index:
            p = pts.loc[idx]
            if any(p.distance(pts.loc[k]) <= metres for k in kept):
                dup.loc[idx] = True
            else:
                kept.append(idx)
    return dup


def classify_places(places: gpd.GeoDataFrame, scoring: Scoring, epsg: int) -> gpd.GeoDataFrame:
    """Return one row per (place, service) candidate with `rule` and `status` columns.

    Expects columns: id, name, confidence, operating_status, taxonomy_primary,
    taxonomy_hierarchy, taxonomy_alternates, geometry. Secondary groups (e.g. convenience)
    get `service` = group name and `secondary` = True. Use `status == "assigned"` for the
    final service set; the other statuses exist for the review table.
    """
    rows = []
    for idx, row in places.iterrows():
        for rule in scoring.services.values():
            groups = [(rule.name, rule.taxonomy, False)]
            groups += [(g, vals, True) for g, vals in rule.secondary.items()]
            for service, taxonomy, secondary in groups:
                rule_for_group = (
                    rule
                    if not secondary
                    else ServiceRule(
                        name=service,
                        taxonomy=taxonomy,
                        min_confidence=rule.min_confidence,
                        exclude_names=rule.exclude_names,
                    )
                )
                m = match_rule(row, rule_for_group, taxonomy)
                if m is not None:
                    rows.append((idx, service, secondary, m[0], m[1]))

    cols = ["service", "secondary", "rule", "status"]
    if not rows:
        out = places.iloc[0:0].copy()
        for c in cols:
            out[c] = pd.Series(dtype=object)
        return out

    hits = pd.DataFrame(rows, columns=["_idx", *cols])
    out = places.loc[hits["_idx"]].reset_index(drop=True)
    out[cols] = hits[cols].to_numpy()
    out["secondary"] = out["secondary"].astype(bool)
    out["name_norm"] = out["name"].map(normalise_name)
    out = out.sort_values(["service", "confidence"], ascending=[True, False], kind="stable")

    assigned = out["status"] == ASSIGNED
    dup = _dedupe(out[assigned], scoring.dedupe_distance_m, epsg)
    out.loc[dup[dup].index, "status"] = ST_DUPLICATE
    return gpd.GeoDataFrame(out.reset_index(drop=True), geometry="geometry", crs=places.crs)
