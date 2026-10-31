"""Equity analysis: who has a short walk to each service, by group, with ACS uncertainty.

Access is measured per home (network distances from Phase 2); groups come from ACS block-group
counts. A group's share within a band is the block-group band shares weighted by the group's count
(assumes group members are spread over a block group's homes like all residents). ACS margins of
error are propagated by Monte Carlo: counts and median income are drawn from Normal(est, MOE/1.645),
clipped at 0, independently (no covariance between numerators and denominators). The draws add noise
to estimates that already contain sampling error, so they mix block groups across terciles and pull
correlations toward 0: read p05–p95 as robustness to ACS error, not as an interval centred on the
point estimate (which can sit at its edge).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import esda
import geopandas as gpd
import libpysal
import numpy as np
import pandas as pd
from scipy import stats
from sqlalchemy import text

from . import db
from .config import CityConfig

Z90 = 1.645  # ACS MOEs are 90 % intervals

# group → ACS count column (its MOE column is <col>_moe)
GROUPS: dict[str, str] = {
    "all_residents": "pop_total",
    "zero_car_households": "hh_no_vehicle",
    "residents_65plus": "pop_65plus",
    "low_income_residents": "pop_below_pov",
}
# block-group rate → (numerator, denominator) counts, for correlations
RATES: dict[str, tuple[str, str]] = {
    "zero_car_hh_pct": ("hh_no_vehicle", "hh_total"),
    "pop_65plus_pct": ("pop_65plus", "pop_total"),
    "below_pov_pct": ("pop_below_pov", "pov_universe"),
}
CLUSTERS = {1: "HH", 2: "LH", 3: "LL", 4: "HL"}  # esda quadrants (value, spatial lag)


def band_tag(m: float) -> str:
    return f"le_{m:g}m"


def origin_indicators(od: pd.DataFrame, bands: list[float]) -> pd.DataFrame:
    """Per origin: boolean `<service>_le_<m>m` for every service and band limit, plus
    `grocery_none_le_<max>m` and `convenience_not_grocery_le_<min>m` when those services exist.

    od: origin_id, service, dist_m (NaN = beyond the last band).
    """
    d = od.pivot(index="origin_id", columns="service", values="dist_m")
    out = {}
    for s in d.columns:
        for m in bands:
            out[f"{s}_{band_tag(m)}"] = d[s] <= m
    near, far = min(bands), max(bands)
    if "grocery" in d:
        out[f"grocery_none_{band_tag(far)}"] = ~(d["grocery"] <= far)
        if "convenience" in d:
            out[f"convenience_not_grocery_{band_tag(near)}"] = (d["convenience"] <= near) & ~(
                d["grocery"] <= near
            )
    return pd.DataFrame(out, index=d.index)


def bg_shares(ind: pd.DataFrame, origins: pd.DataFrame) -> pd.DataFrame:
    """Population-weighted share (0–1) of each indicator per block group (index bg_geoid).

    origins: id, bg_geoid, pop — residential origins only.
    """
    o = origins.set_index("id")[["bg_geoid", "pop"]].join(ind, how="inner")
    w = o["pop"]
    num = o[ind.columns].astype(float).mul(w, axis=0).groupby(o["bg_geoid"]).sum()
    den = w.groupby(o["bg_geoid"]).sum()
    return num.div(den.where(den > 0), axis=0)


def group_shares(shares: pd.DataFrame, counts: np.ndarray | pd.Series) -> np.ndarray:
    """Group-weighted mean of the block-group shares.

    counts: (n_bg,) or (n_draws, n_bg), aligned with `shares.index`. Returns (n_ind,) or
    (n_draws, n_ind). Block groups with a NaN share or count are ignored.
    """
    s = shares.to_numpy(float)
    c = np.atleast_2d(np.asarray(counts, float))
    valid = ~np.isnan(s).any(axis=1)
    c = np.where(np.isnan(c) | ~valid, 0.0, c)
    s = np.where(valid[:, None], s, 0.0)
    tot = c.sum(axis=1, keepdims=True)
    out = (c @ s) / np.where(tot > 0, tot, np.nan)
    return out[0] if np.ndim(counts) == 1 else out


def weighted_terciles(values: pd.Series, weights: pd.Series) -> pd.Series:
    """1/2/3 = lowest/middle/highest third of the total weight, ordered by value.

    A block group goes to the third containing the midpoint of its cumulative weight; equal
    values always share a tercile. NaN values or non-positive weights → <NA>.
    """
    ok = values.notna() & (weights > 0)
    v, w = values[ok], weights[ok].astype(float)
    by = w.groupby(v).sum().sort_index()
    mid = (by.cumsum() - by / 2) / by.sum()
    t = np.minimum(np.floor(3 * mid).astype(int) + 1, 3)
    out = pd.Series(pd.NA, index=values.index, dtype="Int64")
    out[ok] = v.map(t).astype("Int64")
    return out


def draw_acs(
    bg: pd.DataFrame, cols: list[str], n: int, rng: np.random.Generator
) -> dict[str, np.ndarray]:
    """n Monte Carlo draws per column from Normal(est, MOE/1.645), clipped at 0 → (n, n_bg)."""
    out = {}
    for c in cols:
        est = bg[c].to_numpy(float)
        se = np.nan_to_num(bg[f"{c}_moe"].to_numpy(float) / Z90)
        x = rng.normal(est, se, size=(n, len(est)))
        out[c] = np.clip(x, 0, None)
    return out


def _rate(num: np.ndarray, den: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(den > 0, 100 * num / den, np.nan)


def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    ok = ~(np.isnan(x) | np.isnan(y))
    return float(stats.spearmanr(x[ok], y[ok]).statistic) if ok.sum() > 2 else np.nan


def _range(a: np.ndarray, axis: int = 0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Monte Carlo 5th, 50th and 95th percentiles."""
    return tuple(np.nanpercentile(a, q, axis=axis) for q in (5, 50, 95))


def lisa(
    gdf: gpd.GeoDataFrame, col: str, permutations: int, alpha: float, seed: int
) -> tuple[pd.DataFrame, dict]:
    """Global and local Moran's I (Queen contiguity, row-standardised) for `col`.

    Returns per-row cluster labels (HH/LL/HL/LH or "ns", plain and FDR-adjusted) and a summary.
    """
    w = libpysal.weights.Queen.from_dataframe(gdf, use_index=False)
    w.transform = "r"
    y = gdf[col].to_numpy(float)
    np.random.seed(seed)  # esda's global Moran uses the global RNG
    mi = esda.Moran(y, w, permutations=permutations)
    loc = esda.Moran_Local(y, w, permutations=permutations, seed=seed)
    q = pd.Series(loc.q).map(CLUSTERS)
    fdr_p = esda.fdr(loc.p_sim, alpha)
    out = pd.DataFrame(
        {
            "cluster": np.where(loc.p_sim < alpha, q, "ns"),
            "cluster_fdr": np.where(loc.p_sim <= fdr_p, q, "ns"),
            "p": loc.p_sim,
            "local_i": loc.Is,
        },
        index=gdf.index,
    )
    summary = {
        "variable": col,
        "n": int(len(y)),
        "islands": [int(i) for i in w.islands],
        "moran_i": float(mi.I),
        "expected_i": float(mi.EI),
        "p_sim": float(mi.p_sim),
        "clusters": out["cluster"].value_counts().to_dict(),
        "clusters_fdr": out["cluster_fdr"].value_counts().to_dict(),
    }
    return out, summary


def _read(cfg: CityConfig, q: str) -> pd.DataFrame:
    with db.engine().connect() as con:
        return pd.read_sql(text(q.replace("__SCHEMA__", f'"{cfg.slug}"')), con)


def analyse(od: pd.DataFrame, origins: pd.DataFrame, bg: gpd.GeoDataFrame, cfg: CityConfig) -> dict:
    """All equity results (point estimates + Monte Carlo 90 % ranges) as plain Python objects."""
    sc, eq = cfg.scoring, cfg.scoring.equity
    bands = [b for b, _ in sc.bands]
    near, far = min(bands), max(bands)
    n_draws, seed = int(eq["mc_draws"]), int(eq["seed"])
    rng = np.random.default_rng(seed)

    res = origins[origins["kind"] != "centroid"]
    shares = bg_shares(origin_indicators(od, bands), res)
    bg = bg[bg["access_score"].notna()].set_index("geoid")
    bg = bg.loc[bg.index.intersection(shares.index)]
    shares = shares.loc[bg.index]
    score_cols = ["access_score"] + [f"{s}_score" for s in sc.scored_services]
    scores = bg[score_cols]
    ind = list(shares.columns)

    count_cols = sorted({*GROUPS.values(), *(c for pair in RATES.values() for c in pair)})
    draws = draw_acs(bg, count_cols + ["median_hh_income"], n_draws, rng)

    # 1. groups: share within each band per service (+ mean scores), point and range
    rows = []
    for g, col in GROUPS.items():
        point = group_shares(shares, bg[col])
        mc = group_shares(shares, draws[col])
        lo, med, hi = _range(mc)
        sp = group_shares(scores, bg[col])
        smc = group_shares(scores, draws[col])
        slo, smed, shi = _range(smc)
        total = float(bg[col].sum())
        tot_lo, _, tot_hi = _range(draws[col].sum(axis=1))
        for i, name in enumerate(ind):
            rows.append(
                {"group": g, "metric": name, "kind": "pct", "point": 100 * point[i],
                 "p05": 100 * lo[i], "p50": 100 * med[i], "p95": 100 * hi[i],
                 "group_total": total,
                 "group_total_p05": float(tot_lo), "group_total_p95": float(tot_hi)}
            )  # fmt: skip
        for i, name in enumerate(score_cols):
            rows.append(
                {"group": g, "metric": name, "kind": "mean_score", "point": sp[i],
                 "p05": slo[i], "p50": smed[i], "p95": shi[i], "group_total": total,
                 "group_total_p05": float(tot_lo), "group_total_p95": float(tot_hi)}
            )  # fmt: skip
    groups = pd.DataFrame(rows)

    # 2. income terciles (thirds of residents by block-group median household income)
    def tercile_stats(income: np.ndarray, pop: np.ndarray) -> np.ndarray:
        t = weighted_terciles(pd.Series(income, index=bg.index), pd.Series(pop, index=bg.index))
        cols = [f"{s}_{band_tag(near)}" for s in sc.scored_services]
        out = np.full((3, 2 + len(cols)), np.nan)
        for k in (1, 2, 3):
            m = (t == k).to_numpy(dtype=bool, na_value=False)
            if not m.any():
                continue
            w = pop[m]
            out[k - 1, 0] = w.sum()
            out[k - 1, 1] = np.average(scores["access_score"].to_numpy()[m], weights=w)
            out[k - 1, 2:] = 100 * np.average(shares.loc[m, cols].to_numpy(), axis=0, weights=w)
        return out

    terc = weighted_terciles(bg["median_hh_income"], bg["pop_total"])
    t_point = tercile_stats(bg["median_hh_income"].to_numpy(float), bg["pop_total"].to_numpy(float))
    t_mc = np.stack(
        [tercile_stats(draws["median_hh_income"][i], draws["pop_total"][i]) for i in range(n_draws)]
    )
    t_lo, t_med, t_hi = _range(t_mc)
    t_cols = ["residents", "access_score"] + [
        f"{s}_{band_tag(near)}_pct" for s in sc.scored_services
    ]
    terciles = []
    for k in (1, 2, 3):
        inc = bg.loc[(terc == k).to_numpy(dtype=bool, na_value=False), "median_hh_income"]
        row = {"tercile": k, "income_min": float(inc.min()), "income_max": float(inc.max()),
               "n_block_groups": int(len(inc))}  # fmt: skip
        for j, c in enumerate(t_cols):
            row |= {
                c: t_point[k - 1, j],
                f"{c}_p05": t_lo[k - 1, j],
                f"{c}_p50": t_med[k - 1, j],
                f"{c}_p95": t_hi[k - 1, j],
            }
        terciles.append(row)

    # 3. Spearman correlations across block groups
    rates_point = {
        "median_hh_income": bg["median_hh_income"].to_numpy(float),
        **{r: _rate(bg[n].to_numpy(float), bg[d].to_numpy(float)) for r, (n, d) in RATES.items()},
    }
    rates_mc = {
        "median_hh_income": draws["median_hh_income"],
        **{r: _rate(draws[n], draws[d]) for r, (n, d) in RATES.items()},
    }
    corr = []
    for r in rates_point:
        for s in score_cols:
            y = scores[s].to_numpy(float)
            ok = ~np.isnan(rates_point[r])
            res_ = stats.spearmanr(rates_point[r][ok], y[ok])
            mc = np.array([_spearman(rates_mc[r][i], y) for i in range(n_draws)])
            lo, med, hi = _range(mc)
            corr.append(
                {"variable": r, "score": s, "rho": float(res_.statistic),
                 "p_value": float(res_.pvalue), "rho_p05": float(lo), "rho_p50": float(med),
                 "rho_p95": float(hi),
                 "n": int(ok.sum())}
            )  # fmt: skip

    # 4. spatial autocorrelation
    gdf = gpd.GeoDataFrame(bg, geometry="geom").reset_index()
    perms, alpha = int(eq["lisa_permutations"]), float(eq["lisa_alpha"])
    loc_a, moran_a = lisa(gdf, "access_score", perms, alpha, seed)
    loc_g, moran_g = lisa(gdf, "grocery_score", perms, alpha, seed)
    per_bg = pd.DataFrame(
        {
            "geoid": gdf["geoid"],
            "income_tercile": terc.reindex(gdf["geoid"]).to_numpy(),
            "lisa_cluster": loc_a["cluster"],
            "lisa_cluster_fdr": loc_a["cluster_fdr"],
            "lisa_p": loc_a["p"],
            "grocery_lisa_cluster": loc_g["cluster"],
            "grocery_lisa_p": loc_g["p"],
        }
    )

    return {
        "meta": {
            "city": cfg.name,
            "overture_release": cfg.overture_release,
            "acs_year": cfg.acs_year,
            "near_m": near,
            "far_m": far,
            "poverty_ratio": eq["poverty_ratio"],
            "mc_draws": n_draws,
            "seed": seed,
            "lisa_permutations": perms,
            "lisa_alpha": alpha,
            "block_groups": int(len(bg)),
            "residents": float(bg["pop_total"].sum()),
        },
        "groups": groups,
        "terciles": pd.DataFrame(terciles),
        "correlations": pd.DataFrame(corr),
        "moran": [moran_a, moran_g],
        "per_bg": per_bg,
        "shares": shares,
    }


def headline(groups: pd.DataFrame, near: float, far: float) -> list[dict]:
    """All-resident counts and shares for the summary: within `near` per service, no grocery
    within `far`, convenience store but no grocery within `near`."""
    a = groups[(groups["group"] == "all_residents") & (groups["kind"] == "pct")]
    keep = a["metric"].str.endswith(f"_{band_tag(near)}") | a["metric"].isin(
        [f"grocery_none_{band_tag(far)}"]
    )
    out = []
    for r in a[keep].itertuples():
        out.append(
            {"metric": r.metric, "pct": r.point, "pct_p05": r.p05, "pct_p50": r.p50,
             "pct_p95": r.p95,
             "residents": r.point / 100 * r.group_total}
        )  # fmt: skip
    return out


def run(cfg: CityConfig) -> dict:
    """Run the analysis; write <slug>.bg_equity and data/processed/<slug>/equity_* outputs."""
    print("[equity] reading scores and distances …", flush=True)
    od = _read(cfg, "SELECT origin_id, service, dist_m FROM __SCHEMA__.origin_dist")
    origins = _read(cfg, "SELECT id, kind, bg_geoid, pop FROM __SCHEMA__.origins")
    with db.engine().connect() as con:
        bg = gpd.read_postgis(text(f'SELECT * FROM "{cfg.slug}".bg_scores'), con, geom_col="geom")
    print(f"[equity] analysing ({int(cfg.scoring.equity['mc_draws'])} draws) …", flush=True)
    r = analyse(od, origins, bg, cfg)

    cfg.ensure_dirs()
    out = cfg.processed_dir
    r["groups"].to_csv(out / "equity_groups.csv", index=False)
    summary = {
        "meta": r["meta"] | {"created_utc": datetime.now(UTC).isoformat(timespec="seconds")},
        "headline": headline(r["groups"], r["meta"]["near_m"], r["meta"]["far_m"]),
        "groups": r["groups"].to_dict(orient="records"),
        "income_terciles": r["terciles"].to_dict(orient="records"),
        "correlations": r["correlations"].to_dict(orient="records"),
        "moran": r["moran"],
    }
    (out / "equity_summary.json").write_text(json.dumps(summary, indent=2, default=float))
    r["per_bg"].to_parquet(out / "block_groups_equity.parquet", index=False)
    with db.engine().begin() as con:
        r["per_bg"].to_sql("bg_equity", con, schema=cfg.slug, if_exists="replace", index=False)
    print(f"[equity] wrote {out / 'equity_summary.json'}", flush=True)
    return r
